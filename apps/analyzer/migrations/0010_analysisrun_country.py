from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0009_competitor_metadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisrun",
            name="country",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]

