from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analyzer', '0060_recommendation_task_engine_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='useraction',
            name='verification_message',
            field=models.TextField(blank=True, default=''),
        ),
    ]
