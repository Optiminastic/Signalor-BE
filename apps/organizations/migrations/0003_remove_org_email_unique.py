from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0002_alter_organization_options_organization_updated_at_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="organization",
            name="owner_email",
            field=models.EmailField(max_length=254),
        ),
    ]
