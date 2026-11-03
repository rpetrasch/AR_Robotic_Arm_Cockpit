"""The knowledge index (docs/specs/spec_cockpit.md §14.2): chunks with a full-text vector and an
embedding, plus the indexed files with their hashes.

``embedding`` has no dimension here: assistant.index sets ``vector(<dim>)`` and the HNSW index
once it knows the embedding model's dimension, and drops both when the model changes.
"""

from django.db import migrations

CREATE = """
CREATE TABLE kb_files (
    path text PRIMARY KEY,
    hash text NOT NULL,
    chunks integer NOT NULL,
    indexed timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE kb_chunks (
    id bigserial PRIMARY KEY,
    source text NOT NULL REFERENCES kb_files(path) ON DELETE CASCADE,
    kind text NOT NULL,
    heading text NOT NULL,
    position integer NOT NULL,
    text text NOT NULL,
    hash text NOT NULL,
    tokens integer NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', heading), 'A')
        || setweight(to_tsvector('english', text), 'B')
    ) STORED,
    embedding vector
);
CREATE INDEX kb_chunks_tsv ON kb_chunks USING gin (tsv);
CREATE INDEX kb_chunks_source ON kb_chunks (source);
"""


class Migration(migrations.Migration):
    dependencies = [("assistant", "0001_initial"), ("core", "0001_extensions")]

    operations = [migrations.RunSQL(CREATE, "DROP TABLE kb_chunks; DROP TABLE kb_files;")]
