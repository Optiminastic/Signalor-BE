"""Chat conversations: start one, post into one (one reply in flight per brand), read one, list them."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from ...models import CONVERSATION_TITLE_CHARS, AgentConversation, AgentMessage, GrowthProgram

MAX_MESSAGE_CHARS = 2000
THREAD_PAGE_SIZE = 50
CONVERSATION_LIST_SIZE = 30


class ChatError(Exception):
    """The message can't be accepted; the text is safe to show the user."""


def list_conversations(program: GrowthProgram) -> list[AgentConversation]:
    return list(program.conversations.order_by("-updated_at", "-id")[:CONVERSATION_LIST_SIZE])


def get_conversation(program: GrowthProgram, public_id: str) -> AgentConversation | None:
    """Scoped to the program, so another brand's conversation id resolves to None."""
    return program.conversations.filter(public_id=public_id).first()


def thread(conversation: AgentConversation) -> list[AgentMessage]:
    latest = conversation.messages.order_by("-created_at", "-id")[:THREAD_PAGE_SIZE]
    return list(reversed(list(latest)))


def _clean(text: str) -> str:
    content = (text or "").strip()
    if not content:
        raise ChatError("Message is empty.")
    if len(content) > MAX_MESSAGE_CHARS:
        raise ChatError(f"Message is too long (max {MAX_MESSAGE_CHARS} characters).")
    return content


def _create_turn(conversation: AgentConversation, content: str) -> AgentMessage:
    """Inside a transaction: refuses while any reply for the brand is still pending."""
    program = conversation.program
    busy = (
        AgentMessage.objects.select_for_update()
        .filter(program=program, status=AgentMessage.Status.PENDING)
        .exists()
    )
    if busy:
        raise ChatError("I'm still answering your last message.")
    AgentMessage.objects.create(
        program=program, conversation=conversation, role=AgentMessage.Role.USER, content=content
    )
    AgentConversation.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())
    return AgentMessage.objects.create(
        program=program,
        conversation=conversation,
        role=AgentMessage.Role.ASSISTANT,
        status=AgentMessage.Status.PENDING,
    )


def post_message(conversation: AgentConversation, text: str) -> AgentMessage:
    """Stores the user message and a pending assistant reply; returns the reply to run."""
    content = _clean(text)
    with transaction.atomic():
        return _create_turn(conversation, content)


def start_conversation(program: GrowthProgram, text: str) -> tuple[AgentConversation, AgentMessage]:
    """Creates a conversation titled by its first question and posts that question."""
    content = _clean(text)
    with transaction.atomic():
        conversation = AgentConversation.objects.create(
            program=program, title=content[:CONVERSATION_TITLE_CHARS]
        )
        return conversation, _create_turn(conversation, content)
