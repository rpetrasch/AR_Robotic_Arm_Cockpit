"""History queries over the hypertables (docs/specs/spec_cockpit.md §12a.4): the Telemetry tab's
history view and the acceptance checks (sample counts, gaps)."""

from __future__ import annotations

from datetime import datetime

from django.db import connection

MAX_POINTS = 2000  # per joint and request; the bucket grows to fit


def _range(session_id, since, until) -> tuple[datetime, datetime] | None:
    sql = "SELECT min(time), max(time) FROM joint_samples WHERE session_id = %s"
    args: list = [session_id]
    if since:
        sql += " AND time >= %s"
        args.append(since)
    if until:
        sql += " AND time <= %s"
        args.append(until)
    with connection.cursor() as cur:
        cur.execute(sql, args)
        lo, hi = cur.fetchone()
    return (lo, hi) if lo else None


def joints(
    session_id: str,
    since: datetime | None = None,
    until: datetime | None = None,
    bucket_s: float | None = None,
    run_id: str | None = None,
    names: list[str] | None = None,
) -> dict:
    """Per joint and time bucket: position avg / min / max, velocity avg, commanded position.
    Without ``bucket_s`` the bucket keeps the answer under MAX_POINTS per joint."""
    span = _range(session_id, since, until)
    if span is None:
        return {"session": session_id, "bucket_s": bucket_s, "names": [], "series": {}}
    lo, hi = since or span[0], until or span[1]
    seconds = max((hi - lo).total_seconds(), 0.001)
    if not bucket_s:
        bucket_s = max(seconds / MAX_POINTS, 0.02)
    bucket_s = max(float(bucket_s), seconds / (MAX_POINTS * 5), 0.001)
    sql = (
        "SELECT time_bucket(make_interval(secs => %s), time) AS b, joint, avg(position), "
        "min(position), max(position), avg(velocity), avg(cmd_position), count(*) "
        "FROM joint_samples WHERE session_id = %s AND time >= %s AND time <= %s"
    )
    args: list = [bucket_s, session_id, lo, hi]
    if run_id:
        sql += " AND run_id = %s"
        args.append(run_id)
    if names:
        sql += " AND joint = ANY(%s)"
        args.append(names)
    sql += " GROUP BY b, joint ORDER BY b, joint"
    series: dict[str, dict[str, list]] = {}
    with connection.cursor() as cur:
        cur.execute(sql, args)
        for b, joint, pavg, pmin, pmax, vavg, cavg, n in cur.fetchall():
            s = series.setdefault(
                joint, {"t": [], "pos": [], "pos_min": [], "pos_max": [], "vel": [], "cmd": [],
                        "n": []}
            )  # fmt: skip
            s["t"].append(b.timestamp())
            s["pos"].append(pavg)
            s["pos_min"].append(pmin)
            s["pos_max"].append(pmax)
            s["vel"].append(vavg)
            s["cmd"].append(cavg)
            s["n"].append(n)
    return {
        "session": session_id,
        "from": lo.isoformat(),
        "to": hi.isoformat(),
        "bucket_s": bucket_s,
        "names": sorted(series),
        "series": series,
    }


def counts(session_id: str) -> dict:
    """Rows per table and the joint sample rate actually stored (acceptance, §18 C4)."""
    out = {}
    with connection.cursor() as cur:
        for table in ("joint_samples", "gripper_samples", "safety_samples", "events",
                      "trajectories", "metrics"):  # fmt: skip
            cur.execute(f"SELECT count(*) FROM {table} WHERE session_id = %s", [session_id])
            out[table] = cur.fetchone()[0]
        cur.execute(
            "SELECT joint, count(*), min(time), max(time) FROM joint_samples "
            "WHERE session_id = %s GROUP BY joint ORDER BY joint",
            [session_id],
        )
        out["per_joint"] = [
            {"joint": j, "samples": n, "from": a.isoformat(), "to": b.isoformat(),
             "rate_hz": round((n - 1) / max((b - a).total_seconds(), 1e-9), 3) if n > 1 else None}
            for j, n, a, b in cur.fetchall()
        ]  # fmt: skip
    return out


def gaps(session_id: str, joint: str = "joint_1", expected_s: float = 0.02) -> dict:
    """Intervals between consecutive samples of one joint that are longer than 1.5 × the
    expected period."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT t, d FROM (SELECT time AS t, time - lag(time) OVER (ORDER BY time) AS d "
            "FROM joint_samples WHERE session_id = %s AND joint = %s) x "
            "WHERE d > make_interval(secs => %s) ORDER BY d DESC LIMIT 20",
            [session_id, joint, expected_s * 1.5],
        )
        rows = cur.fetchall()
        cur.execute(
            "SELECT count(*) FROM (SELECT time - lag(time) OVER (ORDER BY time) AS d "
            "FROM joint_samples WHERE session_id = %s AND joint = %s) x "
            "WHERE d > make_interval(secs => %s)",
            [session_id, joint, expected_s * 1.5],
        )
        total = cur.fetchone()[0]
    return {
        "joint": joint,
        "gaps": total,
        "largest": [{"at": t.isoformat(), "seconds": d.total_seconds()} for t, d in rows],
    }


def latest_trajectory(session_id: str | None) -> dict | None:
    sql = "SELECT time, payload FROM trajectories"
    args: list = []
    if session_id:
        sql += " WHERE session_id = %s"
        args.append(session_id)
    sql += " ORDER BY time DESC LIMIT 1"
    with connection.cursor() as cur:
        cur.execute(sql, args)
        row = cur.fetchone()
    if not row:
        return None
    import json

    payload = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    return {"time": row[0].isoformat(), **payload}
