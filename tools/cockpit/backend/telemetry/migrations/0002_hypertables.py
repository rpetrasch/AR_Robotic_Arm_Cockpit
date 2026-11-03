"""The time series of §12a.2 as TimescaleDB hypertables, compressed after one day.

Retention (metrics 30 days, the rest unlimited) is not set here: telemetry.policies applies
TELEMETRY_RETENTION from the settings at every run_ingest start, so it stays configurable.
"""

from django.db import migrations

# table -> (columns, chunk interval, compress segmentby)
TABLES = {
    "joint_samples": (
        "session_id uuid NOT NULL, run_id uuid, joint text NOT NULL, position double precision, "
        "velocity double precision, effort double precision, cmd_position double precision, "
        "cmd_velocity double precision",
        "1 hour",
        "session_id, joint",
    ),
    "gripper_samples": (
        "session_id uuid NOT NULL, run_id uuid, joint text NOT NULL, position double precision, "
        "velocity double precision, effort double precision, current double precision",
        "1 hour",
        "session_id",
    ),
    "safety_samples": (
        "session_id uuid NOT NULL, run_id uuid, estop boolean, homed boolean, "
        "in_workspace boolean, faults text[]",
        "1 day",
        "session_id",
    ),
    "metrics": (
        "session_id uuid, name text NOT NULL, labels jsonb NOT NULL DEFAULT '{}', "
        "value double precision",
        "1 day",
        "name",
    ),
    "events": (
        "session_id uuid, run_id uuid, kind text NOT NULL, payload jsonb NOT NULL DEFAULT '{}'",
        "7 days",
        "kind",
    ),
    "detections": ("session_id uuid, run_id uuid, payload jsonb NOT NULL", "1 day", "session_id"),
    "trajectories": (
        "session_id uuid, run_id uuid, payload jsonb NOT NULL",
        "7 days",
        "session_id",
    ),
}


def _create(name: str, columns: str, chunk: str, segmentby: str) -> str:
    return f"""
CREATE TABLE {name} (time timestamptz NOT NULL, {columns});
SELECT create_hypertable('{name}', by_range('time', INTERVAL '{chunk}'));
ALTER TABLE {name} SET (timescaledb.compress, timescaledb.compress_segmentby = '{segmentby}',
                        timescaledb.compress_orderby = 'time');
SELECT add_compression_policy('{name}', INTERVAL '1 day');
"""


SQL = (
    "\n".join(_create(n, *spec) for n, spec in TABLES.items())
    + """
CREATE INDEX joint_samples_session ON joint_samples (session_id, joint, time DESC);
CREATE INDEX gripper_samples_session ON gripper_samples (session_id, time DESC);
CREATE INDEX metrics_name ON metrics (name, time DESC);
CREATE INDEX events_session ON events (session_id, time DESC);
"""
)
REVERSE = "\n".join(f"DROP TABLE IF EXISTS {n} CASCADE;" for n in TABLES)


class Migration(migrations.Migration):
    dependencies = [("telemetry", "0001_initial"), ("core", "0001_extensions")]

    operations = [migrations.RunSQL(SQL, reverse_sql=REVERSE)]
