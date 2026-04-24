# Generated manually — intent + prompt_type on PromptTrack

from django.db import migrations, models


def _backfill_intent_type(apps, schema_editor):
    PromptTrack = apps.get_model("analyzer", "PromptTrack")
    from apps.analyzer.pipeline.prompt_tracker import classify_prompt_intent_and_type

    qs = PromptTrack.objects.select_related("analysis_run").iterator(chunk_size=200)
    for row in qs:
        run = row.analysis_run
        brand = (getattr(run, "brand_name", None) or "").strip()
        url = (getattr(run, "url", None) or "").strip()
        intent, prompt_type = classify_prompt_intent_and_type(row.prompt_text, brand, url)
        PromptTrack.objects.filter(pk=row.pk).update(intent=intent, prompt_type=prompt_type)


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0026_prompttrack_deleted_at_alter_useraction_action_type"),
        ("analyzer", "0026_add_perf_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="prompttrack",
            name="intent",
            field=models.CharField(
                choices=[
                    ("brand", "Brand"),
                    ("informational", "Information"),
                    ("transactional", "Transactional"),
                ],
                default="informational",
                max_length=20,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="prompttrack",
            name="prompt_type",
            field=models.CharField(
                choices=[
                    ("organic", "Organic"),
                    ("branded", "Brand"),
                    ("competitive", "Competition"),
                ],
                default="organic",
                max_length=20,
            ),
            preserve_default=False,
        ),
        migrations.RunPython(_backfill_intent_type, migrations.RunPython.noop),
    ]
