from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0008_promptresult_prompttrack_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="competitor",
            name="tier",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="competitor",
            name="target_market",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="competitor",
            name="geography",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="competitor",
            name="pricing_model",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="competitor",
            name="estimated_revenue_band",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="competitor",
            name="positioning",
            field=models.CharField(blank=True, default="", max_length=240),
        ),
        migrations.AddField(
            model_name="competitor",
            name="relevance_score",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
