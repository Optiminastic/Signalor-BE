from django.db import migrations, models


class Migration(migrations.Migration):
    """Register Recommendation.source in Django's state.

    The `source` column already exists on the shared database (added by another
    branch's migration), so we only sync migration *state* here and run no DB
    operation — preventing a duplicate-column error while letting the ORM insert
    the field (default "analyzer") so NOT-NULL is satisfied.
    """

    dependencies = [
        ("analyzer", "0049_blogpost_delete_satelliteblogpost"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="recommendation",
                    name="source",
                    field=models.CharField(default="analyzer", max_length=20),
                ),
            ],
            database_operations=[],
        ),
    ]
