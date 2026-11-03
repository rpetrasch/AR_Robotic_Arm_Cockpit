"""The rolling log buffer (§10.2): every source's lines, 7 days (telemetry.policies) and at
most LOG_KEEP_PER_SOURCE lines per source (pruned by run_monitor)."""

from django.db import migrations

SQL = """
CREATE TABLE log_lines (
    time timestamptz NOT NULL,
    source text NOT NULL,      -- container:<name> | rosout | job:<id> | cockpit:<process>
    level text NOT NULL,       -- DEBUG INFO WARN ERROR FATAL
    node text NOT NULL DEFAULT '',
    text text NOT NULL,
    session_id uuid
);
SELECT create_hypertable('log_lines', by_range('time', INTERVAL '1 day'));
CREATE INDEX log_lines_source ON log_lines (source, time DESC);
CREATE INDEX log_lines_level ON log_lines (level, time DESC) WHERE level IN ('ERROR', 'FATAL');
CREATE INDEX log_lines_text ON log_lines USING gin (to_tsvector('simple', text));
"""


class Migration(migrations.Migration):
    dependencies = [("monitoring", "0001_initial"), ("core", "0001_extensions")]

    operations = [migrations.RunSQL(SQL, reverse_sql="DROP TABLE IF EXISTS log_lines;")]
