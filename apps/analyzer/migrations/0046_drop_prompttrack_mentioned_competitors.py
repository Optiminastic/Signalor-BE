"""Drop the orphaned `mentioned_competitors` jsonb column on analyzer_prompttrack.

The column was added by an earlier model iteration that has since been removed
from `apps/analyzer/models.PromptTrack`, but no migration was generated to drop
it. Because it's NOT NULL with no default, any new PromptTrack insert via the
ORM fails with `null value in column 'mentioned_competitors' ... violates not-null
constraint`. The competitor-mention surface now lives in PromptCitation
(is_competitor flag) and is computed at read time by CompetitorPromptListView.
"""

from django.db import migrations


def _existing_columns(schema_editor, table):
    return {
        col.name
        for col in schema_editor.connection.introspection.get_table_description(
            schema_editor.connection.cursor(), table
        )
    }


def drop_mentioned_competitors(apps, schema_editor):
    table = "analyzer_prompttrack"
    if "mentioned_competitors" not in _existing_columns(schema_editor, table):
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'ALTER TABLE "{table}" DROP COLUMN "mentioned_competitors";')


def add_mentioned_competitors(apps, schema_editor):
    table = "analyzer_prompttrack"
    if "mentioned_competitors" in _existing_columns(schema_editor, table):
        return
    column_type = "jsonb" if schema_editor.connection.vendor == "postgresql" else "text"
    default = "'[]'::jsonb" if schema_editor.connection.vendor == "postgresql" else "'[]'"
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f'ALTER TABLE "{table}" ADD COLUMN "mentioned_competitors" {column_type} '
            f"NOT NULL DEFAULT {default};"
        )


class Migration(migrations.Migration):

    # Re-pinned onto the current leaf: this migration came over from tushar-05
    # depending on 0036_drop_orphaned_tables, which is NOT part of staging's
    # lineage (that branch's 0036 drops tables that are live models here). The
    # only operation we want is the column drop below, so we chain it as a new
    # tail after the latest merge.
    dependencies = [
        ("analyzer", "0045_merge_20260514_1425"),
    ]

    # Uses RunPython (not RunSQL with `IF EXISTS`) so this also works on
    # SQLite (dev), which doesn't support that clause.
    operations = [
        migrations.RunPython(drop_mentioned_competitors, reverse_code=add_mentioned_competitors),
    ]
