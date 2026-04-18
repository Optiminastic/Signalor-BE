from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0024_prompttrack_5factor_scores_bing_engine"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="brandvisibility",
            name="medium_score",
        ),
        migrations.RemoveField(
            model_name="brandvisibility",
            name="medium_details",
        ),
        migrations.RemoveField(
            model_name="useraction",
            name="action_type",
        ),
        migrations.AddField(
            model_name="useraction",
            name="action_type",
            field=migrations.swappable_dependency("analyzer.UserAction") if False else __import__("django.db.models", fromlist=["CharField"]).CharField(
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
