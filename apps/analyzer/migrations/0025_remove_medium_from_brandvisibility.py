from django.db import migrations, models


def drop_medium_columns(apps, schema_editor):
    table = "analyzer_brandvisibility"
    existing = {
        col.name
        for col in schema_editor.connection.introspection.get_table_description(
            schema_editor.connection.cursor(), table
        )
    }
    with schema_editor.connection.cursor() as cursor:
        if "medium_score" in existing:
            cursor.execute(f'ALTER TABLE "{table}" DROP COLUMN "medium_score";')
        if "medium_details" in existing:
            cursor.execute(f'ALTER TABLE "{table}" DROP COLUMN "medium_details";')


def add_medium_columns(apps, schema_editor):
    table = "analyzer_brandvisibility"
    default_type = "double precision" if schema_editor.connection.vendor == "postgresql" else "real"
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "medium_score" {default_type} NOT NULL DEFAULT 0;')


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0024_prompttrack_5factor_scores_bing_engine"),
    ]

    operations = [
        # Guarded so the migration doesn't crash if the column was already
        # dropped manually or the DB was synced from a newer model state.
        # Uses RunPython (not RunSQL) so the guard works on SQLite (dev) too,
        # since SQLite doesn't support "DROP COLUMN IF EXISTS".
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(drop_medium_columns, reverse_code=add_medium_columns),
            ],
            state_operations=[
                migrations.RemoveField(model_name="brandvisibility", name="medium_score"),
                migrations.RemoveField(model_name="brandvisibility", name="medium_details"),
            ],
        ),
        migrations.RemoveField(
            model_name="useraction",
            name="action_type",
        ),
        migrations.AddField(
            model_name="useraction",
            name="action_type",
            field=models.CharField(
                max_length=30,
                choices=[
                    ("add_faq", "Add FAQ Section"),
                    ("add_structure", "Improve Content Structure"),
                    ("add_citations", "Add Citations & References"),
                    ("improve_readability", "Improve Readability"),
                    ("add_schema", "Add Schema Markup"),
                    ("fix_technical", "Fix Technical Issue"),
                    ("add_author", "Add Author Information"),
                    ("add_about", "Add About Page"),
                    ("add_contact", "Add Contact Page"),
                    ("add_privacy", "Add Privacy Policy"),
                    ("create_wikipedia", "Create Wikipedia Page"),
                    ("add_social", "Add Social Profiles"),
                    ("post_reddit", "Post on Reddit"),
                    ("build_backlinks", "Build Backlinks"),
                ],
                default="add_faq",
            ),
        ),
    ]
