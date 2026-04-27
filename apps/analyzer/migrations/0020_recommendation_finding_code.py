from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0019_analysisrun_onboarding_prompts"),
    ]

    operations = [
        migrations.AddField(
            model_name="recommendation",
            name="finding_code",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
    ]
