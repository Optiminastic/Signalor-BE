"""HNSW ANN index for knowledge-base retrieval (Epic 4).

A partial HNSW index over ``embedding`` with the cosine operator class, matching the
retrieval query's filter (``is_current`` current rows that have an embedding). Built
via ``RunPython`` guarded on the Postgres vendor so it is a clean no-op on SQLite
(local/dev fallback and the test DB) and never touches model state - which keeps
``makemigrations --check`` clean. Requires the ``vector`` extension from 0008.
"""

from django.db import migrations

_INDEX = "corpus_chunk_embedding_hnsw"
_TABLE = "organizations_brandcorpuschunk"

_CREATE = (
    f"CREATE INDEX IF NOT EXISTS {_INDEX} ON {_TABLE} "
    "USING hnsw (embedding vector_cosine_ops) "
    "WHERE is_current AND embedding IS NOT NULL"
)
_DROP = f"DROP INDEX IF EXISTS {_INDEX}"


def _create_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(_CREATE)


def _drop_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(_DROP)


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0008_brandcorpuschunk"),
    ]

    operations = [
        migrations.RunPython(_create_index, _drop_index),
    ]
