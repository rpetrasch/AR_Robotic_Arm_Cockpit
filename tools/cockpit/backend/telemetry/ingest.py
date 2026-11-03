"""run_ingest's work (docs/specs/spec_cockpit.md §12a.3): the agent's telemetry into TimescaleDB,
and the 10 Hz live stream to the browsers.

Two connections to the agent while a stack runs:

- **store**: ``/ws/telemetry?rate=TELEMETRY_RATE_HZ``. A reader task puts every message in a
  queue and never waits for the database; a writer task turns them into rows and writes them
  with COPY, at the latest after ``FLUSH_S`` or ``FLUSH_ROWS`` rows. When the oldest queued
  sample is more than ``LAG_LIMIT_S`` old, the reader keeps only the 10 Hz grid points until
  the lag is below ``LAG_RECOVER_S``; what it leaves out is counted in the session's
  ``dropped`` and the alert ``ingest_lag`` fires (fact ``ingest.lag_s``).
- **live**: ``/ws/telemetry?rate=10`` to the Channels group ``telemetry`` (/ws/telemetry in
  the browser), plus the last 60 s in the cache for a page that opens.

Rows are written only while the session records (REC); the live stream runs regardless.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from datetime import UTC, datetime

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.db.models import F

from core.agent import AgentError, get_agent

from .models import Session

log = logging.getLogger("cockpit.ingest")

FLUSH_S = 1.0
FLUSH_ROWS = 5000
LAG_LIMIT_S = 10.0
LAG_RECOVER_S = 2.0
LIVE_RATE = 10
LIVE_WINDOW_S = 60
LIVE_CACHE_S = 0.2  # a page that opens joins the stream with at most this much missing
RETRY_S = 2.0
STATE_KEY = "cockpit:ingest"
LIVE_KEY = "cockpit:telemetry:live"
GROUP = "telemetry"

JOINT_COLS = (
    "time",
    "session_id",
    "run_id",
    "joint",
    "position",
    "velocity",
    "effort",
    "cmd_position",
    "cmd_velocity",
)
GRIPPER_COLS = ("time", "session_id", "run_id", "joint", "position", "velocity", "effort")
SAFETY_COLS = ("time", "session_id", "run_id", "estop", "homed", "in_workspace", "faults")
METRIC_COLS = ("time", "session_id", "name", "labels", "value")
PAYLOAD_COLS = ("time", "session_id", "run_id", "payload")
EVENT_COLS = ("time", "session_id", "run_id", "kind", "payload")


def is_gripper(joint: str) -> bool:
    return "gripper" in joint


def _ts(t: float) -> datetime:
    return datetime.fromtimestamp(t, UTC)


def _at(values: list | None, i: int):
    return values[i] if values is not None and i < len(values) else None


def rows_for(msg: dict, session_id, run_id=None) -> dict[str, list[tuple]]:
    """The rows of one agent message, per table."""
    import json

    out: dict[str, list[tuple]] = {
        "joint_samples": [],
        "gripper_samples": [],
        "safety_samples": [],
        "metrics": [],
        "trajectories": [],
        "detections": [],
        "events": [],
    }
    names = msg.get("names") or []
    for s in msg.get("samples", []):
        t = _ts(s["t"])
        p, v, e, cp, cv = s.get("p"), s.get("v"), s.get("e"), s.get("cp"), s.get("cv")
        for i, joint in enumerate(names):
            if is_gripper(joint):
                out["gripper_samples"].append(
                    (t, session_id, run_id, joint, _at(p, i), _at(v, i), _at(e, i))
                )
            else:
                out["joint_samples"].append(
                    (t, session_id, run_id, joint, _at(p, i), _at(v, i), _at(e, i), _at(cp, i),
                     _at(cv, i))
                )  # fmt: skip
        if "s" in s:
            st = s["s"]
            out["safety_samples"].append(
                (t, session_id, run_id, st["estop"], st["homed"], st["in_workspace"], st["faults"])
            )
    now = datetime.now(UTC)
    for topic, rate in (msg.get("rates") or {}).items():
        labels = json.dumps({"topic": topic})
        out["metrics"].append((now, session_id, "topic_hz", labels, rate.get("hz")))
    if msg.get("rtf") is not None:
        out["metrics"].append((now, session_id, "sim_rtf", "{}", msg["rtf"]))
    for ev in msg.get("events", []):
        t = _ts(ev.get("t", time.time()))
        payload = {k: v for k, v in ev.items() if k not in ("t", "kind")}
        if ev["kind"] == "trajectory":
            out["trajectories"].append((t, session_id, run_id, json.dumps(payload)))
            points = len(payload.get("points", []))
            out["events"].append(
                (t, session_id, run_id, "trajectory", json.dumps({"points": points}))
            )
        elif ev["kind"] == "detections":
            out["detections"].append((t, session_id, run_id, json.dumps(payload)))
        else:
            out["events"].append((t, session_id, run_id, ev["kind"], json.dumps(payload)))
    return out


COLUMNS = {
    "joint_samples": JOINT_COLS,
    "gripper_samples": GRIPPER_COLS,
    "safety_samples": SAFETY_COLS,
    "metrics": METRIC_COLS,
    "trajectories": PAYLOAD_COLS,
    "detections": PAYLOAD_COLS,
    "events": EVENT_COLS,
}


def decimate(msg: dict, rate: int, keep_rate: int) -> tuple[dict, int]:
    """Only the samples on the ``keep_rate`` grid (back-pressure); returns (msg, dropped)."""
    every = max(1, rate // keep_rate)
    kept = [s for s in msg.get("samples", []) if round(s["t"] * rate) % every == 0]
    dropped = len(msg.get("samples", [])) - len(kept)
    return {**msg, "samples": kept}, dropped


def dsn(app: str = "cockpit run_ingest") -> str:
    db = settings.DATABASES["default"]
    from psycopg.conninfo import make_conninfo

    return make_conninfo(
        host=db["HOST"],
        port=db["PORT"],
        dbname=db["NAME"],
        user=db["USER"],
        password=db["PASSWORD"],
        application_name=app,
    )


class Store:
    """Batched COPY into the hypertables over its own psycopg connection."""

    def __init__(self):
        self._conn = None

    async def _connection(self):
        import psycopg

        if self._conn is None or self._conn.closed:
            self._conn = await psycopg.AsyncConnection.connect(dsn(), autocommit=True)
        return self._conn

    async def write(self, rows: dict[str, list[tuple]]) -> int:
        conn = await self._connection()
        n = 0
        async with conn.transaction():
            for table, batch in rows.items():
                if not batch:
                    continue
                cols = ", ".join(COLUMNS[table])
                async with conn.cursor() as cur:
                    async with cur.copy(f"COPY {table} ({cols}) FROM STDIN") as copy:
                        for row in batch:
                            await copy.write_row(row)
                n += len(batch)
        return n

    async def close(self):
        if self._conn is not None:
            await self._conn.close()


class Recorder:
    """The store connection of one session: reader → queue → writer."""

    def __init__(self, session: Session, agent=None, store: Store | None = None):
        self.session = session
        self.agent = agent or get_agent()
        self.store = store or Store()
        self.rate = session.rate_hz
        self.queue: deque[dict] = deque()
        self.degraded = False
        self.recording = session.recording
        self.written = 0  # joint ticks written
        self.dropped = 0
        self.lag_s = 0.0
        self.connected = False
        self.run_id = None  # the scenario run in progress (scenarios.execute), tags the rows
        self.pending: dict[str, list[tuple]] = {}  # rows taken from the queue, not written yet
        self.pending_rows = 0
        self.pending_ticks = 0
        self._wake = asyncio.Event()

    def lag(self) -> float:
        oldest = next((m["samples"][0]["t"] for m in self.queue if m.get("samples")), None)
        return max(0.0, time.time() - oldest) if oldest else 0.0

    def offer(self, msg: dict) -> None:
        """Reader side: never waits. Applies the 10 Hz back-pressure rule."""
        self.lag_s = self.lag()
        if not self.degraded and self.lag_s > LAG_LIMIT_S:
            self.degraded = True
            log.warning("ingest lags %.1f s: storing 10 Hz until it catches up", self.lag_s)
        elif self.degraded and self.lag_s < LAG_RECOVER_S:
            self.degraded = False
            log.info("ingest caught up: storing %d Hz again", self.rate)
        if self.degraded:
            msg, dropped = decimate(msg, self.rate, LIVE_RATE)
            self.dropped += dropped
        self.queue.append(msg)
        self._wake.set()

    async def read(self) -> None:
        async for msg in self.agent.telemetry(self.rate):
            self.connected = True
            if self.recording:
                self.offer(msg)

    def _take(self, limit: int | None = FLUSH_ROWS) -> None:
        """Queue → pending rows (at most ``limit`` rows; None: everything)."""
        while self.queue and (limit is None or self.pending_rows < limit):
            msg = self.queue.popleft()
            self.pending_ticks += len(msg.get("samples", []))
            for table, rows in rows_for(msg, self.session.id, self.run_id).items():
                self.pending.setdefault(table, []).extend(rows)
                self.pending_rows += len(rows)

    async def flush(self) -> None:
        if not self.pending_rows:
            return
        await self.store.write(self.pending)
        ticks = self.pending_ticks
        self.pending, self.pending_rows, self.pending_ticks = {}, 0, 0
        await Session.objects.filter(pk=self.session.pk).aupdate(
            samples=F("samples") + ticks, dropped=self.dropped
        )
        self.written += ticks

    async def write_loop(self) -> None:
        last = time.monotonic()
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), FLUSH_S)
            self._wake.clear()
            self._take()
            if self.pending_rows >= FLUSH_ROWS or time.monotonic() - last >= FLUSH_S:
                await self.flush()
                last = time.monotonic()

    def state(self) -> dict:
        return {
            "session": str(self.session.id),
            "recording": self.recording,
            "rate_hz": self.rate,
            "connected": self.connected,
            "lag_s": round(self.lag(), 2),
            "degraded": self.degraded,
            "written": self.written,
            "dropped": self.dropped,
            "queued": len(self.queue),
        }


async def live(agent=None) -> None:
    """The 10 Hz stream to the browsers, and the last 60 s for a page that opens."""
    from channels.layers import get_channel_layer

    agent = agent or get_agent()
    layer = get_channel_layer()
    window: deque[dict] = deque(maxlen=LIVE_RATE * LIVE_WINDOW_S)
    names: list[str] = []
    last_cache = 0.0
    async for msg in agent.telemetry(LIVE_RATE):
        names = msg.get("names") or names
        window.extend(msg.get("samples", []))
        await layer.group_send(GROUP, {"type": "telemetry.message", "data": msg})
        if time.monotonic() - last_cache >= LIVE_CACHE_S:
            last_cache = time.monotonic()
            await cache.aset(
                LIVE_KEY,
                {"names": names, "samples": list(window), "rates": msg.get("rates")},
                timeout=30,
            )


async def _retrying(name: str, factory, status: dict) -> None:
    """Run ``factory()`` again whenever the agent stream fails (it is not up yet, restarted)."""
    while True:
        try:
            await factory()
            status[name] = "stream ended"
        except AgentError as exc:
            status[name] = exc.detail or exc.title
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a bad message must not stop the worker
            log.exception("%s failed", name)
            status[name] = str(exc)
        await asyncio.sleep(RETRY_S)


class Ingest:
    """run_ingest's loop: follow the running stack's session, start and stop the tasks."""

    def __init__(self, agent=None, store_factory=Store):
        self.agent = agent
        self.store_factory = store_factory
        self.recorder: Recorder | None = None
        self.tasks: list[asyncio.Task] = []
        self.status: dict = {}

    async def step(self, st, host=None) -> None:
        from . import sessions

        session = await sessions.sync(st, host)
        same = (
            self.recorder is not None
            and session is not None
            and (self.recorder.session.id == session.id)
        )
        if not same:
            await self.stop()
            if session is not None:
                self._start(session)
        if self.recorder is not None and session is not None:
            fresh = (
                await Session.objects.filter(pk=session.pk)
                .values_list("recording", flat=True)
                .afirst()
            )
            self.recorder.recording = bool(fresh)
            self.recorder.run_id = await cache.aget("cockpit:run:current")
        state = {**(self.recorder.state() if self.recorder else {"session": None}),
                 "streams": dict(self.status)}  # fmt: skip
        await cache.aset(STATE_KEY, state, timeout=30)

    def _start(self, session: Session) -> None:
        log.info("session %s (%s): recording %s", session.id, session.mode, session.recording)
        self.recorder = Recorder(session, self.agent or get_agent(), self.store_factory())
        rec = self.recorder

        async def store():
            rec.connected = False
            await rec.read()

        self.status = {}
        self.tasks = [
            asyncio.ensure_future(_retrying("store", store, self.status)),
            asyncio.ensure_future(rec.write_loop()),
            asyncio.ensure_future(
                _retrying("live", lambda: live(self.agent or get_agent()), self.status)
            ),
        ]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        if self.recorder is not None:
            await self._drain()
            await self.recorder.store.close()
        self.tasks, self.recorder = [], None
        await cache.adelete(LIVE_KEY)

    async def _drain(self) -> None:
        """Write what is still queued or pending when the stack goes away."""
        rec = self.recorder
        rec._take(limit=None)
        try:
            await rec.flush()
        except Exception:
            log.exception("could not write the last %d samples", rec.pending_ticks)


async def ingest_state() -> dict:
    return await cache.aget(STATE_KEY) or {"session": None, "worker": "not running"}


@sync_to_async
def set_recording(on: bool) -> Session | None:
    session = Session.objects.filter(ended__isnull=True).order_by("-started").first()
    if session is None:
        return None
    session.recording = on
    session.save(update_fields=["recording"])
    return session
