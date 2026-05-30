"""Reconcile a small set of known migration-drift cases before `migrate` runs.

Two opposite kinds of drift are handled:

1. Record missing, object present (RECONCILIATIONS). If a schema object exists
   on disk but its migration record is missing from ``django_migrations``,
   ``migrate`` will try to create it again and crash ("relation already exists"
   for CreateModel, "column already exists" for AddField). We insert the missing
   record so the real ``migrate`` invocation can proceed.

   Each entry must be safe in ALL three scenarios:
     - drifted (object exists, record missing) → insert the record
     - fresh DB (object missing, record missing) → no-op, real migrate creates it
     - healthy (object exists, record present) → no-op

2. Object missing, record present (RECREATE_IF_DROPPED). A historical deploy of
   the tushar-05 branch ran ``analyzer.0036_drop_orphaned_tables`` against the
   shared staging DB, which ``DROP TABLE … CASCADE``'d a set of analyzer tables
   that are live models on this branch. The CreateModel records still say
   "applied", so ``migrate`` never recreates them and every query 500s with
   "relation … does not exist". We rebuild each missing table from the CURRENT
   model state via the schema editor (so it picks up columns added by later
   migrations too), leaving the migration records untouched.

   Each entry is safe in the same three scenarios:
     - drifted (table missing, create-migration recorded) → recreate table
     - fresh DB (table missing, nothing recorded yet) → no-op, real migrate creates it
     - healthy (table exists) → no-op

Add entries here only after confirming the matching migration is the
historical record of what's on disk — re-faking a different schema will
hide real divergence.
"""

from __future__ import annotations

import os
import sys

import django

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", os.environ.get("DJANGO_SETTINGS_MODULE", "config.settings.production")
)
django.setup()

from django.db import connection  # noqa: E402  (must come after django.setup)

# Each entry is one of:
#   ("table", table_name, app_label, migration_name)
#       — fake the migration if `table_name` exists in public schema
#   ("column", table_name, column_name, app_label, migration_name)
#       — fake the migration if `column_name` exists on `table_name`
RECONCILIATIONS: list[tuple] = [
    # Staging picked up the public_api.0003 table outside the normal migration
    # history, so subsequent deploys re-attempted CREATE TABLE and failed.
    ("table", "public_api_nextjsdeployment", "public_api", "0003_nextjsdeployment"),
    # Staging's organizations_organization table already has the normalized_url
    # column from a previous arkit-01 deploy that ran on the shared DB, but
    # the staging branch never recorded the matching 0004 migration. Without
    # this entry, migrate would try AddField again and fail.
    (
        "column",
        "organizations_organization",
        "normalized_url",
        "organizations",
        "0004_organization_normalized_url_and_more",
    ),
]

# Tables that tushar-05's 0036_drop_orphaned_tables dropped from the shared
# staging DB but which are live models on this branch. Each entry is
#   (app_label, ModelName, creating_migration_name)
# and is recreated from current model state iff the create-migration is
# recorded but the physical table is missing. Order is irrelevant — the runner
# retries across passes so intra-set FKs (provider→product→order) resolve
# themselves; everything else FKs only to already-present tables (analysisrun,
# prompttrack, organization).
RECREATE_IF_DROPPED: list[tuple[str, str, str]] = [
    ("analyzer", "BacklinkProvider", "0037_backlinkprovider_backlinkproduct_backlinkorder_and_more"),
    ("analyzer", "BacklinkProduct", "0037_backlinkprovider_backlinkproduct_backlinkorder_and_more"),
    ("analyzer", "BacklinkOrder", "0037_backlinkprovider_backlinkproduct_backlinkorder_and_more"),
    ("analyzer", "BacklinkSnapshot", "0035_backlinksnapshot"),
    ("analyzer", "BacklinkOpportunity", "0036_backlinkopportunity"),
    ("analyzer", "PromptSchemaArtifact", "0038_promptschemaartifact"),
    ("analyzer", "BrandKit", "0039_alter_autofixjob_status_brandkit_and_more"),
    ("analyzer", "PromptWikipediaDraft", "0039_alter_autofixjob_status_brandkit_and_more"),
    ("analyzer", "ChatMessage", "0039_alter_autofixjob_status_brandkit_and_more"),
    ("analyzer", "DomainAnalyticsSnapshot", "0040_domainanalyticssnapshot"),
    ("analyzer", "ContentSuggestion", "0043_contentsuggestion"),
]


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SELECT to_regclass(%s)", [f"public.{table_name}"])
    return cursor.fetchone()[0] is not None


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
        [table_name, column_name],
    )
    return cursor.fetchone() is not None


def _migration_recorded(cursor, app_label: str, migration_name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM django_migrations WHERE app = %s AND name = %s",
        [app_label, migration_name],
    )
    return cursor.fetchone() is not None


def _record_migration(cursor, app_label: str, migration_name: str, why: str) -> None:
    cursor.execute(
        "INSERT INTO django_migrations (app, name, applied) VALUES (%s, %s, NOW())",
        [app_label, migration_name],
    )
    print(
        f"reconcile_migrations: faked {app_label}.{migration_name} ({why})",
        file=sys.stderr,
    )


def _recreate_dropped_tables(cursor) -> None:
    """Rebuild tables that were dropped out-of-band but are still live models.

    Iterates in passes so a model whose FK target is itself being recreated
    (provider→product→order) succeeds once its dependency lands. A model is a
    candidate only when its create-migration is recorded (so a fresh DB, where
    nothing is recorded yet, is left entirely to the real ``migrate``).
    """
    from django.apps import apps

    pending = []
    for app_label, model_name, migration_name in RECREATE_IF_DROPPED:
        model = apps.get_model(app_label, model_name)
        table = model._meta.db_table
        if _table_exists(cursor, table):
            continue  # healthy → no-op
        if not _migration_recorded(cursor, app_label, migration_name):
            continue  # fresh DB → let migrate create it
        pending.append((model, table))

    last_error: Exception | None = None
    made_progress = True
    while pending and made_progress:
        made_progress = False
        still_pending = []
        for model, table in pending:
            try:
                with connection.schema_editor(atomic=True) as editor:
                    editor.create_model(model)
            except Exception as exc:  # noqa: BLE001 — likely a not-yet-created FK target
                still_pending.append((model, table))
                last_error = exc
                continue
            made_progress = True
            print(
                f"reconcile_migrations: recreated dropped table {table!r} "
                f"from current {model.__name__} state",
                file=sys.stderr,
            )
        pending = still_pending

    if pending:
        names = ", ".join(table for _, table in pending)
        print(
            f"reconcile_migrations: WARNING could not recreate {names} (last error: {last_error})",
            file=sys.stderr,
        )


def main() -> None:
    if connection.vendor != "postgresql":
        # The SQL below is Postgres-specific. SQLite dev environments don't
        # hit this drift; they always run migrations from scratch.
        return

    with connection.cursor() as cursor:
        for entry in RECONCILIATIONS:
            kind = entry[0]
            if kind == "table":
                _, table_name, app_label, migration_name = entry
                if not _table_exists(cursor, table_name):
                    continue
                if _migration_recorded(cursor, app_label, migration_name):
                    continue
                _record_migration(
                    cursor, app_label, migration_name, why=f"table {table_name!r} already exists"
                )
            elif kind == "column":
                _, table_name, column_name, app_label, migration_name = entry
                if not _table_exists(cursor, table_name):
                    continue
                if not _column_exists(cursor, table_name, column_name):
                    continue
                if _migration_recorded(cursor, app_label, migration_name):
                    continue
                _record_migration(
                    cursor,
                    app_label,
                    migration_name,
                    why=f"column {table_name}.{column_name!r} already exists",
                )
            else:
                raise RuntimeError(f"unknown reconciliation kind: {kind!r}")

        _recreate_dropped_tables(cursor)


if __name__ == "__main__":
    main()
