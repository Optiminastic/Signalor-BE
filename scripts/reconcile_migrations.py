"""Reconcile a small set of known migration-drift cases before `migrate` runs.

If a table exists on disk but its migration record is missing from
``django_migrations``, ``migrate`` will try to CREATE TABLE again and crash
with "relation already exists". This script inserts the missing record so
the real ``migrate`` invocation can proceed.

Each entry must be safe in ALL three scenarios:
  - drifted (table exists, record missing) → insert the record
  - fresh DB (table missing, record missing) → no-op, real migrate creates it
  - healthy (table exists, record present) → no-op

Add entries here only after confirming the matching CreateModel migration is
the historical record of the table on disk — re-faking a different schema
will hide real divergence.
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

RECONCILIATIONS: list[tuple[str, str, str]] = [
    # (table_name, app_label, migration_name)
    # Staging picked up the public_api.0003 table outside the normal migration
    # history, so subsequent deploys re-attempted CREATE TABLE and failed.
    ("public_api_nextjsdeployment", "public_api", "0003_nextjsdeployment"),
]


def main() -> None:
    if connection.vendor != "postgresql":
        # The SQL below is Postgres-specific. SQLite dev environments don't
        # hit this drift; they always run migrations from scratch.
        return

    with connection.cursor() as cursor:
        for table_name, app_label, migration_name in RECONCILIATIONS:
            cursor.execute("SELECT to_regclass(%s)", [f"public.{table_name}"])
            table_exists = cursor.fetchone()[0] is not None
            if not table_exists:
                continue

            cursor.execute(
                "SELECT 1 FROM django_migrations WHERE app = %s AND name = %s",
                [app_label, migration_name],
            )
            already_recorded = cursor.fetchone() is not None
            if already_recorded:
                continue

            cursor.execute(
                "INSERT INTO django_migrations (app, name, applied) VALUES (%s, %s, NOW())",
                [app_label, migration_name],
            )
            print(
                f"reconcile_migrations: faked {app_label}.{migration_name} "
                f"(table {table_name!r} already exists)",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
