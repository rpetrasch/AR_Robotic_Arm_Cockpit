"""TimescaleDB and pgvector in the cockpit database (docs/specs/spec_cockpit.md §5, §12a, §14.2).

The hypertables (C4) and the assistant index (C6) build on these; C1 only checks that the
pinned image provides both.
"""

from django.db import migrations


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.RunSQL(
            "CREATE EXTENSION IF NOT EXISTS timescaledb; CREATE EXTENSION IF NOT EXISTS vector;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
