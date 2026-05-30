"""Reconcile a small set of known migration-drift cases before `migrate` runs.

If a schema object exists on disk but its migration record is missing from
``django_migrations``, ``migrate`` will try to create it again and crash
("relation already exists" for CreateModel, "column already exists" for
AddField). This script inserts the missing record so the real ``migrate``
invocation can proceed.

Each entry must be safe in ALL three scenarios:
  - drifted (object exists, record missing) → insert the record
  - fresh DB (object missing, record missing) → no-op, real migrate creates it
  - healthy (object exists, record present) → no-op

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


if __name__ == "__main__":
    main()
