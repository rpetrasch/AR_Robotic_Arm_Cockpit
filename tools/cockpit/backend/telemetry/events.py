"""The ``events`` hypertable (docs/specs/spec_cockpit.md §12a.1): mode changes, arming steps,
alerts, job results, e-stop transitions and run steps, on the same timeline as the telemetry."""

from __future__ import annotations

import json
from datetime import datetime

from asgiref.sync import sync_to_async
from django.db import connection
from django.utils import timezone


def record_sync(kind: str, payload: dict, session=None, run=None, at: datetime | None = None):
    session_id = getattr(session, "id", session)
    if session_id is None:
        from .models import Session

        open_session = Session.objects.filter(ended__isnull=True).order_by("-started").first()
        session_id = open_session.id if open_session else None
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO events (time, session_id, run_id, kind, payload) "
            "VALUES (%s, %s, %s, %s, %s)",
            [
                at or timezone.now(),
                session_id,
                getattr(run, "id", run),
                kind,
                json.dumps(payload, default=str),
            ],
        )


async def record(kind: str, payload: dict, session=None, run=None, at=None) -> None:
    await sync_to_async(record_sync)(kind, payload, session, run, at)


def query(session_id=None, since=None, until=None, kinds=None, limit: int = 500) -> list[dict]:
    sql = "SELECT time, session_id, run_id, kind, payload FROM events WHERE TRUE"
    args: list = []
    if session_id:
        sql += " AND session_id = %s"
        args.append(session_id)
    if since:
        sql += " AND time >= %s"
        args.append(since)
    if until:
        sql += " AND time <= %s"
        args.append(until)
    if kinds:
        sql += " AND kind = ANY(%s)"
        args.append(list(kinds))
    sql += " ORDER BY time DESC LIMIT %s"
    args.append(limit)
    with connection.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    return [
        {
            "time": t.isoformat(),
            "session_id": str(s) if s else None,
            "run_id": str(r) if r else None,
            "kind": k,
            "payload": p if isinstance(p, dict) else json.loads(p),
        }
        for t, s, r, k, p in rows
    ]
