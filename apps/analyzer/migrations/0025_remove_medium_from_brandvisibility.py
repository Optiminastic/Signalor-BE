from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0024_prompttrack_5factor_scores_bing_engine"),
    ]

    operations = [
        # Use IF EXISTS so the migration doesn't crash if the column was already
        # dropped manually or the DB was synced from a newer model state.
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql='ALTER TABLE "analyzer_brandvisibility" DROP COLUMN IF EXISTS "medium_score";',
                    reverse_sql='ALTER TABLE "analyzer_brandvisibility" ADD COLUMN "medium_score" double precision NOT NULL DEFAULT 0;',
                ),
                migrations.RunSQL(
                    sql='ALTER TABLE "analyzer_brandvisibility" DROP COLUMN IF EXISTS "medium_details";',
                    reverse_sql="",
                ),
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
