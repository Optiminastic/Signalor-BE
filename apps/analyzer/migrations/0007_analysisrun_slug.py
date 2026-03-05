import secrets

from django.db import migrations, models


def populate_slugs(apps, schema_editor):
    AnalysisRun = apps.get_model("analyzer", "AnalysisRun")
    used = set()
    for run in AnalysisRun.objects.all():
        while True:
            candidate = secrets.token_urlsafe(8)
            if candidate not in used:
                used.add(candidate)
                break
        run.slug = candidate
        run.save(update_fields=["slug"])


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0006_blog_automation"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisrun",
            name="slug",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.RunPython(populate_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="analysisrun",
            name="slug",
            field=models.CharField(max_length=20, unique=True),
        ),
        migrations.AddIndex(
            model_name="analysisrun",
            index=models.Index(fields=["slug"], name="analyzer_an_slug_idx"),
        ),
    ]
