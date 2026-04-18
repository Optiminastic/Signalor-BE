from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("visibility", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="visibilitycheck",
            name="medium_score",
        ),
        migrations.RemoveField(
            model_name="visibilitycheck",
            name="medium_details",
        ),
        migrations.AlterField(
            model_name="visibilitycheck",
            name="status",
            field=__import__("django.db.models", fromlist=["CharField"]).CharField(
                choices=[
                    ("pending", "Pending"),
                    ("checking_google", "Checking Google"),
                    ("checking_reddit", "Checking Reddit"),
                    ("scoring", "Scoring"),
                    ("complete", "Complete"),
                    ("failed", "Failed"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
