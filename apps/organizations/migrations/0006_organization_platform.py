from django.db import migrations, models


class Migration(migrations.Migration):
    """Add ``Organization.platform`` to the model state.

    The column already exists (NOT NULL varchar(20)) on the shared database via
    out-of-band schema drift, while the model on ``main`` never declared it -- so
    ``Organization.objects.create(...)`` omitted it and hit a NotNullViolation on
    onboarding. This adds the model field and reconciles the schema idempotently:
    the ``ADD COLUMN IF NOT EXISTS`` is a no-op on the drifted database and creates
    the column on a fresh one, so the same migration is safe everywhere without a
    manual ``--fake``.
    """

    dependencies = [
        ("organizations", "0005_organization_slug"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE organizations_organization "
                        "ADD COLUMN IF NOT EXISTS platform varchar(20) NOT NULL DEFAULT '';"
                    ),
                    reverse_sql=(
                        "ALTER TABLE organizations_organization "
                        "DROP COLUMN IF EXISTS platform;"
                    ),
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="organization",
                    name="platform",
                    field=models.CharField(blank=True, default="", max_length=20),
                ),
            ],
        ),
    ]
