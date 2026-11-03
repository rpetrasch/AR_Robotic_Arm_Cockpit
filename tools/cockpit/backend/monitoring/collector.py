"""run_monitor's log collector (docs/specs/spec_cockpit.md §10.2): one ``docker logs -f`` per
running ar3 container and the agent's /rosout stream, into the log sink. The error patterns (§8.3)
run over every line; error and critical matches feed the alert rule log_error_pattern.

A container's stream starts where the stored lines end (``--since`` the newest stored line of
that source), so a monitor restart neither loses nor repeats lines; a new container starts
with its last ``FIRST_TAIL`` lines.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from datetime import UTC, datetime, timedelta

from asgiref.sync import sync_to_async
from django.db import connection

from core.agent import AgentError, get_agent
from setup import patterns

from . import logs

log = logging.getLogger("cockpit.monitor")
FIRST_TAIL = 500
MATCHES_KEEP = 200
RETRY_S = 3.0
ALERT_SEVERITIES = ("error", "critical")


def _since_arg(t: datetime) -> str:
    """RFC 3339 with nanoseconds, 1 µs after ``t`` (docker's --since is inclusive)."""
    t = (t + timedelta(microseconds=1)).astimezone(UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"


@sync_to_async
def _newest(source: str) -> datetime | None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT max(time) FROM log_lines "
            "WHERE source = %s AND time > now() - interval '7 days'",
            [source],
        )
        row = cur.fetchone()
    return row[0] if row else None


class Collector:
    def __init__(self, runner, sink: logs.LogSink | None = None, agent=None):
        self.runner = runner
        self.sink = sink or logs.sink()
        self.agent = agent
        self.streams: dict[str, asyncio.Task] = {}
        self.rosout_task: asyncio.Task | None = None
        self.rosout_seq = 0
        self.matches: deque[dict] = deque(maxlen=MATCHES_KEEP)  # not yet seen by the alerts
        self.session_id = None

    def _line(self, source: str, level: str, text: str, node: str, at) -> None:
        self.sink.push(source, level, text, node=node, at=at, session=self.session_id)
        for hit in patterns.match(text):
            if hit["severity"] in ALERT_SEVERITIES:
                self.matches.append(
                    {
                        "id": hit["id"],
                        "title": hit["title"],
                        "severity": hit["severity"],
                        "hint": hit["hint"],
                        "source": source,
                        "line": patterns.ANSI.sub("", text)[:500],
                        "time": at.isoformat(),
                    }
                )

    def take_matches(self) -> list[dict]:
        out = list(self.matches)
        self.matches.clear()
        return out

    async def _follow(self, name: str) -> None:
        source = f"container:{name}"
        newest = await _newest(source)
        argv = ["docker", "logs", "-f", "--timestamps"]
        argv += ["--since", _since_arg(newest)] if newest else ["--tail", str(FIRST_TAIL)]

        async def on_line(_stream: str, raw: str) -> None:
            at, text = logs.parse_docker(raw)
            level, node, text = logs.parse_ros(text)
            self._line(source, level, text, node, at)

        await self.runner.stream([*argv, name], on_line, timeout=10**9)

    async def _rosout(self) -> None:
        agent = self.agent or get_agent()
        # The agent keeps its last lines; after a monitor restart they are stored already.
        stored = await _newest("rosout") if self.rosout_seq == 0 else None
        async for msg in agent.rosout(self.rosout_seq):
            self.rosout_seq = msg.get("seq", self.rosout_seq)
            for line in msg.get("lines", []):
                t = line.get("t") or 0
                # /rosout stamps are wall time; a sim-time stamp (small) falls back to now
                at = datetime.fromtimestamp(t, UTC) if t > 1e9 else datetime.now(UTC)
                if stored is not None and at <= stored:
                    continue
                self._line("rosout", line.get("level", "INFO"), line.get("text", ""),
                           line.get("node", ""), at)  # fmt: skip

    async def _rosout_forever(self) -> None:
        while True:
            try:
                await self._rosout()
            except AgentError:
                pass
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("rosout stream failed")
            await asyncio.sleep(RETRY_S)

    async def step(self, st, session_id=None) -> None:
        """Follow the running containers; /rosout while a stack runs."""
        self.session_id = session_id
        running = {c.name for c in st.containers if c.state == "running"} if st.docker else set()
        for name, task in list(self.streams.items()):
            if task.done():
                del self.streams[name]
        for name in running - set(self.streams):
            self.streams[name] = asyncio.ensure_future(self._follow(name))
        stack_up = st.docker and st.stack is not None and st.stack[1].state == "running"
        if stack_up and self.rosout_task is None:
            self.rosout_seq = 0
            self.rosout_task = asyncio.ensure_future(self._rosout_forever())
        elif not stack_up and self.rosout_task is not None:
            self.rosout_task.cancel()
            self.rosout_task = None

    async def stop(self) -> None:
        tasks = [*self.streams.values(), *([self.rosout_task] if self.rosout_task else [])]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self.streams, self.rosout_task = {}, None
