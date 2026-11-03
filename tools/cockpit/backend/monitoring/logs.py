"""Logs (docs/specs/spec_cockpit.md §10.2): every source into the ``log_lines`` hypertable, search,
live tail.

Sources (``source`` column):

- ``container:<name>``: ``docker logs -f --timestamps`` of every ar3 container (run_monitor);
- ``rosout``: the agent's /rosout stream, with node name and level (run_monitor);
- ``job:<id>``: job output (run_jobs, setup.executor);
- ``cockpit:<process>``: the cockpit's own log records (web, monitor, jobs, ingest).

Every process has one ``LogSink``: a thread that writes the queued lines with COPY every
``FLUSH_S`` and sends them to the Channels group ``logs`` for the live tail. Raw Docker logs
stay the source of truth; this is a rolling buffer (7 days, at most LOG_KEEP_PER_SOURCE lines
per source, pruned by run_monitor).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from datetime import UTC, datetime

from django.conf import settings
from django.db import DatabaseError, connection, transaction

GROUP = "logs"
FLUSH_S = 0.5
QUEUE_MAX = 20000  # per process; the oldest lines go first when the database is away
LEVELS = ("DEBUG", "INFO", "WARN", "ERROR", "FATAL")
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}
MAX_TEXT = 4000
# "[move_group-4] [INFO] [1730000000.123] [move_group]: text" and plain "[WARN] [..] [node]: …"
_ROS = re.compile(
    r"\[(DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\]\s+\[[\d.]+\]\s+\[([\w/.~-]+)\]:\s?(.*)$"
)
_LAUNCH = re.compile(r"^\[([\w.-]+?)(?:-\d+)?\]\s")
# launch's own lines: "[ERROR] [ros2_control_node-1]: process has died [pid 5, exit code -11]"
_LAUNCH_OWN = re.compile(
    r"^\[(DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\]\s+\[([\w.-]+?)(?:-\d+)?\]:\s?"
)
_DOCKER_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)\s?(.*)$")
_BRACKET = re.compile(r"\[(DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\]")
_FAILURE = re.compile(r"^Traceback |^\w*(Error|Exception): |\berror: ")
SKIP_LOGGERS = ("cockpit.logsink", "psycopg", "asyncio", "django.db.backends")

log = logging.getLogger("cockpit.logsink")


def norm_level(level: str) -> str:
    level = level.upper()
    return {"WARNING": "WARN", "CRITICAL": "FATAL"}.get(level, level if level in LEVELS else "INFO")


def parse_ros(text: str) -> tuple[str, str, str]:
    """(level, node, text) of a container line: the ROS console format, a bracketed level,
    a Python traceback; INFO otherwise. The node is the ROS node, else the launch process."""
    m = _ROS.search(text)
    if m:
        return norm_level(m.group(1)), m.group(2), text
    own = _LAUNCH_OWN.match(text)
    if own:
        return norm_level(own.group(1)), own.group(2), text
    launch = _LAUNCH.match(text)
    node = launch.group(1) if launch and launch.group(1) not in LEVELS else ""
    bracket = _BRACKET.search(text)
    if bracket:
        return norm_level(bracket.group(1)), node, text
    if _FAILURE.search(text):
        return "ERROR", node, text
    return "INFO", node, text


def parse_docker(line: str) -> tuple[datetime, str]:
    """A ``docker logs --timestamps`` line → (time, text)."""
    m = _DOCKER_TS.match(line)
    if not m:
        return datetime.now(UTC), line
    stamp = m.group(1).rstrip("Z")
    head, _dot, frac = stamp.partition(".")
    t = datetime.fromisoformat(head + (("." + frac[:6]) if frac else "")).replace(tzinfo=UTC)
    return t, m.group(2)


class LogSink:
    """Thread-safe queue + writer thread for this process's log lines. ``thread=False``: no
    writer thread, the caller flushes (tests); the process-wide sink is inactive in the unit
    tests, which write their own."""

    def __init__(self, thread: bool = True, active: bool = True):
        self.thread = thread
        self.active = active
        self._queue: deque[tuple] = deque(maxlen=QUEUE_MAX)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._conn = None
        self.dropped = 0
        self.written = 0
        self.listeners: list = []  # callables(lines) in the writer thread (error patterns)

    def push(self, source: str, level: str, text: str, node: str = "", at=None, session=None):
        if not self.active:
            return
        with self._lock:
            if len(self._queue) == self._queue.maxlen:
                self.dropped += 1
            self._queue.append(
                (at or datetime.now(UTC), source, norm_level(level), node or "",
                 text[:MAX_TEXT], session)
            )  # fmt: skip
        self._ensure_thread()

    def _ensure_thread(self) -> None:
        if not self.thread:
            return
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="logsink", daemon=True)
            self._thread.start()

    def _take(self) -> list[tuple]:
        with self._lock:
            batch = list(self._queue)
            self._queue.clear()
        return batch

    def flush(self) -> int:
        batch = self._take()
        if not batch:
            return 0
        try:
            self._write(batch)
        except Exception:
            self._conn = None
            with self._lock:  # keep them for the next try, oldest first
                self._queue.extendleft(reversed(batch))
            raise
        self.written += len(batch)
        self._publish(batch)
        for listener in self.listeners:
            listener(batch)
        return len(batch)

    def _write(self, batch: list[tuple]) -> None:
        import psycopg

        from telemetry.ingest import dsn

        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(dsn("cockpit logs"), autocommit=True)
        with self._conn.cursor() as cur:
            with cur.copy(
                "COPY log_lines (time, source, level, node, text, session_id) FROM STDIN"
            ) as copy:
                for row in batch:
                    copy.write_row(row)

    def _publish(self, batch: list[tuple]) -> None:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is None:
            return
        lines = [line_dict(row) for row in batch[-500:]]
        try:
            async_to_sync(layer.group_send)(GROUP, {"type": "logs.lines", "lines": lines})
        except Exception:  # the live tail is best effort; the lines are stored
            pass

    def _run(self) -> None:
        while True:
            time.sleep(FLUSH_S)
            try:
                self.flush()
            except Exception as exc:  # the database is away: keep the lines, retry
                print(f"cockpit log sink: {exc}", flush=True)  # noqa: T201 (not via logging)
                time.sleep(2.0)


_sink: LogSink | None = None
_sink_lock = threading.Lock()


def sink() -> LogSink:
    global _sink
    with _sink_lock:
        if _sink is None:
            _sink = LogSink(active=not settings.TESTING)
        return _sink


class DbHandler(logging.Handler):
    """The cockpit's own log records into log_lines (``cockpit:<process>``)."""

    def __init__(self, process: str):
        super().__init__(level=logging.INFO)
        self.source = f"cockpit:{process}"

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(SKIP_LOGGERS):
            return
        try:
            text = self.format(record) if self.formatter else record.getMessage()
            if record.exc_info:
                text += "\n" + logging.Formatter().formatException(record.exc_info)
            sink().push(self.source, record.levelname, text, node=record.name)
        except Exception:
            self.handleError(record)


def install_handler(process: str) -> None:
    """Called by each worker command and the ASGI app at startup."""
    root = logging.getLogger()
    if any(isinstance(h, DbHandler) for h in root.handlers):
        return
    root.addHandler(DbHandler(process))


def line_dict(row) -> dict:
    t, source, level, node, text, _session = row
    return {"time": t.isoformat(), "source": source, "level": level, "node": node, "text": text}


# --- search (API) --------------------------------------------------------------------------


def query(
    sources: list[str] | None = None,
    level: str | None = None,
    node: str | None = None,
    q: str | None = None,
    regex: bool = False,
    since=None,
    until=None,
    limit: int = 1000,
    before=None,
) -> list[dict]:
    """Newest first. ``sources``: exact names or prefixes ending in ':' (``container:``);
    ``level``: the minimum; ``q``: case-insensitive text, or a POSIX regex with ``regex``."""
    sql = "SELECT time, source, level, node, text FROM log_lines WHERE TRUE"
    args: list = []
    if sources:
        parts = []
        for s in sources:
            if s.endswith(":"):
                parts.append("source LIKE %s")
                args.append(s.replace("%", r"\%") + "%")
            else:
                parts.append("source = %s")
                args.append(s)
        sql += " AND (" + " OR ".join(parts) + ")"
    if level:
        wanted = LEVELS[LEVEL_RANK.get(norm_level(level), 0) :]
        sql += " AND level = ANY(%s)"
        args.append(list(wanted))
    if node:
        sql += " AND node ILIKE %s"
        args.append(f"%{node}%")
    if q:
        if regex:
            sql += " AND text ~* %s"
            args.append(q)
        else:
            sql += " AND text ILIKE %s"
            args.append("%" + q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%")
    if since:
        sql += " AND time >= %s"
        args.append(since)
    if until:
        sql += " AND time <= %s"
        args.append(until)
    if before:
        sql += " AND time < %s"
        args.append(before)
    sql += " ORDER BY time DESC LIMIT %s"
    args.append(limit)
    try:
        # a savepoint: an invalid regex must not break the caller's transaction
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
    except DatabaseError as exc:
        if regex and "regular expression" in str(exc):
            raise InvalidRegex(str(exc).splitlines()[0]) from None
        raise
    return [
        {"time": t.isoformat(), "source": s, "level": lv, "node": n, "text": x}
        for t, s, lv, n, x in rows
    ]


class InvalidRegex(ValueError):
    pass


def sources() -> list[dict]:
    """Sources with their line count and newest line (last 7 days)."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT source, count(*), max(time) FROM log_lines "
            "WHERE time > now() - interval '7 days' GROUP BY source ORDER BY source"
        )
        return [
            {"source": s, "lines": n, "last": t.isoformat() if t else None}
            for s, n, t in cur.fetchall()
        ]


def prune(keep: int | None = None) -> int:
    """At most ``keep`` lines per source (§10.2). The 7 days are a retention policy."""
    keep = keep or settings.LOG_KEEP_PER_SOURCE
    deleted = 0
    with connection.cursor() as cur:
        cur.execute("SELECT DISTINCT source FROM log_lines")
        for (source,) in cur.fetchall():
            cur.execute(
                "SELECT time FROM log_lines WHERE source = %s ORDER BY time DESC "
                "OFFSET %s LIMIT 1",
                [source, keep],
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    "DELETE FROM log_lines WHERE source = %s AND time <= %s", [source, row[0]]
                )
                deleted += cur.rowcount
    return deleted


def as_text(lines: list[dict]) -> str:
    return "".join(
        f"{x['time']} {x['level']:<5} {x['source']} {x['node'] + ': ' if x['node'] else ''}"
        f"{x['text']}\n"
        for x in reversed(lines)
    )
