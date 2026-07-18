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

# CONCURRENTLY so building the index does not take an ACCESS EXCLUSIVE lock on the
# table (which would block all reads and writes on organizations_brandcorpuschunk
# for the whole build — a multi-minute outage on a large corpus during a routine
# deploy). Requires the migration to run outside a transaction (atomic = False).
_CREATE = (
    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX} ON {_TABLE} "
    "USING hnsw (embedding vector_cosine_ops) "
    "WHERE is_current AND embedding IS NOT NULL"
)
_DROP = f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}"


def _disable_timeouts(schema_editor):
    """Lift the per-connection statement/lock timeouts for this session.

    Production sets a global ``statement_timeout`` (config/settings/production.py) on
    every connection, including the one running migrations. A CONCURRENTLY index build
    on a large corpus can exceed that and be killed mid-build, failing the deploy — so
    we disable the ceilings for this migration's session only (it runs non-atomically,
    so the SET persists for the build)."""
    schema_editor.execute("SET statement_timeout = 0")
    schema_editor.execute("SET lock_timeout = 0")


def _create_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    _disable_timeouts(schema_editor)
    schema_editor.execute(_CREATE)


def _drop_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    _disable_timeouts(schema_editor)
    schema_editor.execute(_DROP)


class Migration(migrations.Migration):
    # CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction, so this
    # migration must not be wrapped in one. (On SQLite the RunPython bodies are a
    # vendor-guarded no-op, so non-atomic is harmless there.)
    atomic = False

    dependencies = [
        ("organizations", "0008_brandcorpuschunk"),
    ]

    operations = [
        migrations.RunPython(_create_index, _drop_index),
    ]
