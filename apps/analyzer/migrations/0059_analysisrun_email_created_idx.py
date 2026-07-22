"""Composite index for the email-scoped run list.

``AnalysisRunListView`` runs ``filter(email=...).order_by("-created_at")`` — the
organization path already has a matching ``(organization, -created_at)`` index but
the email path only had ``(email)`` / ``(email, status)``, neither of which covers
the sort. This adds the mirror ``(email, created_at DESC)`` index.

Built with ``CREATE INDEX CONCURRENTLY`` (raw SQL, vendor-guarded) so a large
``analyzer_analysisrun`` table is not locked for the whole build during a deploy —
same pattern as organizations/0009. ``SeparateDatabaseAndState`` keeps Django's
migration state aligned with the model's ``Meta.indexes`` so ``makemigrations
--check`` stays clean.
"""

from django.db import migrations, models

_INDEX = "idx_run_email_created"
_TABLE = "analyzer_analysisrun"

_CREATE = f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX} ON {_TABLE} (email, created_at DESC)"
_DROP = f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}"


def _lift_timeouts(schema_editor):
    # Production sets a global statement/lock timeout on every connection; a
    # CONCURRENTLY build on a large table can exceed it and be killed mid-build.
    schema_editor.execute("SET statement_timeout = 0")
    schema_editor.execute("SET lock_timeout = 0")


def _create_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    _lift_timeouts(schema_editor)
    schema_editor.execute(_CREATE)


def _drop_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    _lift_timeouts(schema_editor)
    schema_editor.execute(_DROP)


class Migration(migrations.Migration):
    # CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("analyzer", "0058_shopifyproduct"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(_create_index, _drop_index)],
            state_operations=[
                migrations.AddIndex(
                    model_name="analysisrun",
                    index=models.Index(
                        fields=["email", "-created_at"], name="idx_run_email_created"
                    ),
                ),
            ],
        ),
    ]
