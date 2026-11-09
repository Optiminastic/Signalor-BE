import django.db.models.deletion
from django.db import migrations, models

from apps.growth_agent.models import new_conversation_id

TITLE_CHARS = 80


def move_messages_into_conversations(apps, schema_editor):
    """Each program's existing single thread becomes its first saved conversation."""
    AgentMessage = apps.get_model("growth_agent", "AgentMessage")
    AgentConversation = apps.get_model("growth_agent", "AgentConversation")
    # order_by() clears the model's default ordering, which would otherwise make
    # distinct() return one program id per message.
    program_ids = set(
        AgentMessage.objects.filter(conversation__isnull=True).order_by().values_list("program_id", flat=True)
    )
    for program_id in program_ids:
        orphans = AgentMessage.objects.filter(program_id=program_id, conversation__isnull=True)
        first_question = orphans.filter(role="user").order_by("created_at", "id").first()
        title = (first_question.content if first_question else "Earlier chat")[:TITLE_CHARS]
        conversation = AgentConversation.objects.create(
            program_id=program_id,
            public_id=_unused_id(AgentConversation),
            title=title,
        )
        orphans.update(conversation=conversation)


def _unused_id(model) -> str:
    while True:
        candidate = new_conversation_id()
        if not model.objects.filter(public_id=candidate).exists():
            return candidate


class Migration(migrations.Migration):
    dependencies = [
        ("growth_agent", "0007_outreach"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentConversation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "public_id",
                    models.CharField(
                        default=new_conversation_id,
                        editable=False,
                        max_length=16,
                        unique=True,
                    ),
                ),
                ("title", models.CharField(blank=True, default="", max_length=TITLE_CHARS)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "program",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conversations",
                        to="growth_agent.growthprogram",
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at", "-id"],
                "indexes": [
                    models.Index(fields=["program", "-updated_at"], name="growth_agen_program_conv_idx")
                ],
            },
        ),
        migrations.AddField(
            model_name="agentmessage",
            name="conversation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="messages",
                to="growth_agent.agentconversation",
            ),
        ),
        migrations.RunPython(move_messages_into_conversations, migrations.RunPython.noop),
    ]
