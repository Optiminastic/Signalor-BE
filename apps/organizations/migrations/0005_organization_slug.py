import secrets

from django.db import migrations, models


def _gen_slug() -> str:
    return secrets.token_urlsafe(16)


def backfill_slugs(apps, schema_editor):
    """Give every existing org a unique, unguessable slug."""
    Organization = apps.get_model("organizations", "Organization")
    taken = set(Organization.objects.exclude(slug="").values_list("slug", flat=True))
    for org in Organization.objects.filter(slug=""):
        candidate = _gen_slug()
        while candidate in taken:
            candidate = _gen_slug()
        taken.add(candidate)
        org.slug = candidate
        org.save(update_fields=["slug"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0004_organization_normalized_url_and_more"),
    ]

    operations = [
        # 1) Add the column non-unique so existing rows (default "") don't clash.
        migrations.AddField(
            model_name="organization",
            name="slug",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        # 2) Backfill unique slugs for existing rows.
        migrations.RunPython(backfill_slugs, noop),
        # 3) Now that every row has a distinct value, enforce uniqueness + index.
        migrations.AlterField(
            model_name="organization",
            name="slug",
            field=models.CharField(
                blank=True, db_index=True, default="", max_length=32, unique=True
            ),
        ),
    ]
