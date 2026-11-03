"""Retention and compression of the hypertables (docs/specs/spec_cockpit.md §12a.2).

Compression after one day is set by the migration. Retention comes from
TELEMETRY_RETENTION ("metrics=30,log_lines=7"; tables not listed keep everything) and is
applied by ``apply()`` at every run_ingest start, so changing .env and restarting is enough.
"""

from __future__ import annotations

from django.conf import settings
from django.db import connection

HYPERTABLES = (
    "joint_samples",
    "gripper_samples",
    "safety_samples",
    "metrics",
    "events",
    "detections",
    "trajectories",
    "log_lines",
)


def retention() -> dict[str, int]:
    """table -> days (0: keep everything)."""
    out = dict.fromkeys(HYPERTABLES, 0)
    for part in settings.TELEMETRY_RETENTION.split(","):
        name, sep, days = part.strip().partition("=")
        if not sep:
            continue
        if name not in out:
            raise ValueError(f"TELEMETRY_RETENTION: unknown table {name!r}")
        out[name] = 0 if days.strip().lower() in ("0", "off", "") else int(days)
    return out


def apply() -> dict[str, int]:
    """Make the retention policies match the settings; returns what is in force."""
    wanted = retention()
    with connection.cursor() as cur:
        for table, days in wanted.items():
            cur.execute("SELECT remove_retention_policy(%s, if_exists => true)", [table])
            if days:
                cur.execute(
                    "SELECT add_retention_policy(%s, make_interval(days => %s))", [table, days]
                )
    return wanted


def compress(everything: bool = False) -> list[dict]:
    """Run the compression policies now (they compress chunks older than one day).
    ``everything``: compress every chunk of the sample tables, also today's (to check the
    compression of a fresh session, §18 C4). Inserts into a compressed chunk still work.

    The policy procedure commits per chunk, which PostgreSQL allows only outside a
    transaction block, so the jobs run over their own autocommit connection."""
    if everything:
        with connection.cursor() as cur:
            for table in HYPERTABLES:
                if table == "log_lines":
                    continue  # not compressed: it is pruned and searched
                cur.execute(
                    "SELECT compress_chunk(c, if_not_compressed => true) FROM show_chunks(%s) c",
                    [table],
                )
    else:
        import psycopg

        from .ingest import dsn

        with psycopg.connect(dsn("cockpit compress"), autocommit=True) as conn:
            jobs = conn.execute(
                "SELECT job_id FROM timescaledb_information.jobs "
                "WHERE proc_name = 'policy_compression'"
            ).fetchall()
            for (job_id,) in jobs:
                conn.execute("CALL run_job(%s::integer)", [job_id])
    return stats()


def stats() -> list[dict]:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT hypertable_name, "
            "count(*) FILTER (WHERE is_compressed), count(*) "
            "FROM timescaledb_information.chunks GROUP BY hypertable_name ORDER BY 1"
        )
        chunks = {name: (c, n) for name, c, n in cur.fetchall()}
        out = []
        for table in HYPERTABLES:
            cur.execute("SELECT * FROM hypertable_compression_stats(%s)", [table])
            row = cur.fetchone()
            compressed, total = chunks.get(table, (0, 0))
            before, after = (row[5], row[9]) if row and row[5] is not None else (None, None)
            out.append(
                {
                    "table": table,
                    "chunks": total,
                    "compressed_chunks": compressed,
                    "bytes_before": before,
                    "bytes_after": after,
                }
            )
        return out
