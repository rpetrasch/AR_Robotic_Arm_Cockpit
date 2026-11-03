"""Continuous aggregates of the joint samples (§12a.2): 1 s and 1 min stats per joint.

A continuous aggregate cannot be created inside a transaction, hence ``atomic = False``.
"""

from django.db import migrations

STATS = """
SELECT time_bucket(INTERVAL '{bucket}', time) AS bucket, session_id, joint,
       count(*) AS samples,
       avg(position) AS position_avg, min(position) AS position_min, max(position) AS position_max,
       avg(velocity) AS velocity_avg, max(abs(velocity)) AS velocity_abs_max,
       avg(abs(position - cmd_position)) AS tracking_error_avg,
       max(abs(position - cmd_position)) AS tracking_error_max
FROM joint_samples GROUP BY bucket, session_id, joint
"""


def _view(name: str, bucket: str, start: str, end: str, every: str) -> str:
    return f"""
CREATE MATERIALIZED VIEW {name} WITH (timescaledb.continuous) AS {STATS.format(bucket=bucket)}
WITH NO DATA;
SELECT add_continuous_aggregate_policy('{name}', start_offset => INTERVAL '{start}',
    end_offset => INTERVAL '{end}', schedule_interval => INTERVAL '{every}');
"""


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("telemetry", "0002_hypertables")]

    operations = [
        migrations.RunSQL(
            _view("joint_stats_1s", "1 second", "1 hour", "1 second", "1 minute"),
            reverse_sql="DROP MATERIALIZED VIEW IF EXISTS joint_stats_1s;",
        ),
        migrations.RunSQL(
            _view("joint_stats_1m", "1 minute", "1 day", "1 minute", "5 minutes"),
            reverse_sql="DROP MATERIALIZED VIEW IF EXISTS joint_stats_1m;",
        ),
    ]
