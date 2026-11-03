"""/ws/logs (live tail, §10.2) and /ws/telemetry (live plots, §10.3) for signed-in users.

/ws/logs?source=…&level=…&q=…: every new line of the log sinks that passes the filters
(the same meaning as GET /api/logs). /ws/telemetry: on connect the last 60 s (from run_ingest's
cache), then the agent's 10 Hz messages as run_ingest relays them.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.cache import cache

from telemetry import ingest

from . import logs


class _Authenticated(AsyncJsonWebsocketConsumer):
    group = ""

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            # Accept first: a socket closed before the handshake reaches the browser as a
            # failed connection (HTTP 403) without the code; the client needs the code.
            await self.accept()
            await self.close(code=4401)
            return
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.on_open()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group, self.channel_name)

    async def on_open(self):
        pass


def line_filter(query: dict[str, list[str]]):
    """A predicate over line dicts from /ws/logs's query string."""
    sources = [s for v in query.get("source", []) for s in v.split(",") if s]
    level = (query.get("level") or [""])[0]
    node = (query.get("node") or [""])[0].lower()
    q = (query.get("q") or [""])[0]
    regex = (query.get("regex") or ["0"])[0] == "1"
    rank = logs.LEVEL_RANK.get(logs.norm_level(level), 0) if level else 0
    try:
        pattern = re.compile(q, re.I) if q and regex else None
    except re.error:
        pattern = None
    q_low = q.lower()

    def ok(line: dict) -> bool:
        if sources and not any(
            line["source"].startswith(s) if s.endswith(":") else line["source"] == s
            for s in sources
        ):
            return False
        if logs.LEVEL_RANK.get(line["level"], 0) < rank:
            return False
        if node and node not in line["node"].lower():
            return False
        if pattern is not None:
            return bool(pattern.search(line["text"]))
        return not q_low or q_low in line["text"].lower()

    return ok


class LogsConsumer(_Authenticated):
    group = logs.GROUP

    async def on_open(self):
        self.ok = line_filter(parse_qs(self.scope.get("query_string", b"").decode()))

    async def logs_lines(self, event):
        lines = [x for x in event["lines"] if self.ok(x)]
        if lines:
            await self.send_json({"type": "lines", "lines": lines})


class TelemetryConsumer(_Authenticated):
    group = ingest.GROUP

    async def on_open(self):
        window = await cache.aget(ingest.LIVE_KEY)
        await self.send_json({"type": "window", "data": window or {"names": [], "samples": []}})

    async def telemetry_message(self, event):
        await self.send_json({"type": "telemetry", "data": event["data"]})
