"""Export of a session (or run, or time range) to Parquet or CSV (docs/specs/spec_cockpit.md
§12a.4).

One directory per export under COCKPIT_EXPORT_DIR (default ``data/exports/``):

    <session-start>_<mode>_<id8>/
        session.json           the session row, the time range, the files
        joint_samples.parquet  time, session_id, run_id, joint, position, velocity, effort,
                               cmd_position, cmd_velocity (long format, one row per joint)
        gripper_samples.parquet, safety_samples.parquet, events.parquet (when not empty)

Times are UTC (``timestamp[us, tz=UTC]`` in Parquet, ISO 8601 in CSV). The same code runs for
the UI button (step ``telemetry.export``), ``ar3ctl telemetry-export`` and
``make telemetry-export``. Rows are read in batches with a server-side cursor, so a long
session does not have to fit in memory.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction

from .models import Session

FORMATS = ("parquet", "csv")
BATCH = 50_000
TABLES = {
    "joint_samples": (
        "time, session_id, run_id, joint, position, velocity, effort, cmd_position, cmd_velocity"
    ),
    "gripper_samples": "time, session_id, run_id, joint, position, velocity, effort",
    "safety_samples": "time, session_id, run_id, estop, homed, in_workspace, faults",
    "events": "time, session_id, run_id, kind, payload",
}


class ExportError(ValueError):
    pass


def _schema(table: str):
    import pyarrow as pa

    ts = pa.timestamp("us", tz="UTC")
    f64 = pa.float64()
    common = [("time", ts), ("session_id", pa.string()), ("run_id", pa.string())]
    rest = {
        "joint_samples": [("joint", pa.string())] + [(c, f64) for c in (
            "position", "velocity", "effort", "cmd_position", "cmd_velocity")],
        "gripper_samples": [("joint", pa.string()), ("position", f64), ("velocity", f64),
                            ("effort", f64)],
        "safety_samples": [("estop", pa.bool_()), ("homed", pa.bool_()),
                           ("in_workspace", pa.bool_()), ("faults", pa.list_(pa.string()))],
        "events": [("kind", pa.string()), ("payload", pa.string())],
    }[table]  # fmt: skip
    return pa.schema(common + rest)


def _clean(row: tuple) -> list:
    out = []
    for v in row:
        if hasattr(v, "hex") and not isinstance(v, bytes | str | float | int):  # uuid
            v = str(v)
        elif isinstance(v, dict):
            v = json.dumps(v)
        out.append(v)
    return out


def _where(session_id, run_id, since, until) -> tuple[str, list]:
    sql, args = " WHERE session_id = %s", [session_id]
    if run_id:
        sql += " AND run_id = %s"
        args.append(run_id)
    if since:
        sql += " AND time >= %s"
        args.append(since)
    if until:
        sql += " AND time <= %s"
        args.append(until)
    return sql, args


def _write_table(table: str, path: Path, fmt: str, where: str, args: list) -> int:
    cols = TABLES[table]
    rows = 0
    writer = fh = None
    with transaction.atomic(), connection.cursor() as cur:
        # a named (server-side) cursor streams the rows in batches
        cur.execute(
            f"DECLARE export_cur NO SCROLL CURSOR FOR SELECT {cols} FROM {table}{where} "
            "ORDER BY time" + (", joint" if "joint" in cols else ""),
            args,
        )
        try:
            while True:
                cur.execute(f"FETCH {BATCH} FROM export_cur")
                batch = [_clean(r) for r in cur.fetchall()]
                if not batch:
                    break
                if fmt == "parquet":
                    import pyarrow as pa
                    import pyarrow.parquet as pq

                    schema = _schema(table)
                    columns = list(zip(*batch, strict=True))
                    arrays = [
                        pa.array(list(col), type=field.type)
                        for col, field in zip(columns, schema, strict=True)
                    ]
                    if writer is None:
                        writer = pq.ParquetWriter(path, schema, compression="zstd")
                    writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
                else:
                    if writer is None:
                        fh = path.open("w", newline="")
                        writer = csv.writer(fh)
                        writer.writerow([c.strip() for c in cols.split(",")])
                    writer.writerows(
                        [v.isoformat() if isinstance(v, datetime) else v for v in r] for r in batch
                    )
                rows += len(batch)
        finally:
            cur.execute("CLOSE export_cur")
            if fmt == "parquet" and writer is not None:
                writer.close()
            if fh is not None:
                fh.close()
    return rows


def export(
    session_id: str,
    fmt: str = "parquet",
    run_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    out_dir: Path | None = None,
    log=print,
) -> Path:
    """Write the export; returns its directory. ``log(line)`` reports progress."""
    if fmt not in FORMATS:
        raise ExportError(f"format must be one of {FORMATS}")
    session = Session.objects.filter(pk=session_id).first()
    if session is None:
        raise ExportError(f"no session {session_id}")
    base = Path(out_dir or settings.EXPORT_DIR)
    name = f"{session.started:%Y%m%d-%H%M%S}_{session.mode}_{str(session.id)[:8]}"
    if run_id or since or until:
        name += f"_{datetime.now(UTC):%H%M%S}"
    target = base / name
    target.mkdir(parents=True, exist_ok=True)
    where, args = _where(str(session.id), run_id, since, until)
    files = {}
    for table in TABLES:
        path = target / f"{table}.{fmt}"
        n = _write_table(table, path, fmt, where, args)
        if n:
            files[path.name] = n
            log(f"{path.name}: {n} rows")
    from .sessions import to_dict

    meta = {
        "session": to_dict(session),
        "run_id": run_id,
        "from": since.isoformat() if since else None,
        "to": until.isoformat() if until else None,
        "format": fmt,
        "files": files,
        "exported": datetime.now(UTC).isoformat(),
        "note": "times are UTC; joint_samples is long format (one row per joint and sample)",
    }
    (target / "session.json").write_text(json.dumps(meta, indent=2))
    log(f"export: {target}")
    return target


def list_exports() -> list[dict]:
    base = Path(settings.EXPORT_DIR)
    out = []
    for meta_path in sorted(base.glob("*/session.json"), reverse=True):
        try:
            meta = json.loads(meta_path.read_text())
        except ValueError:
            continue
        files = [
            {"name": p.name, "size": p.stat().st_size}
            for p in sorted(meta_path.parent.iterdir())
            if p.is_file()
        ]
        out.append(
            {
                "name": meta_path.parent.name,
                "session": meta.get("session", {}).get("id"),
                "format": meta.get("format"),
                "exported": meta.get("exported"),
                "files": files,
            }
        )
    return out
