"""Chat thread lifecycle: post a message (one reply in flight at a time) and read the thread."""

from __future__ import annotations

from django.db import transaction

from ...models import AgentMessage, GrowthProgram

MAX_MESSAGE_CHARS = 2000
THREAD_PAGE_SIZE = 50


class ChatError(Exception):
    """The message can't be accepted; the text is safe to show the user."""


def thread(program: GrowthProgram) -> list[AgentMessage]:
    latest = program.messages.order_by("-created_at", "-id")[:THREAD_PAGE_SIZE]
    return list(reversed(list(latest)))


def post_message(program: GrowthProgram, text: str) -> AgentMessage:
    """Stores the user message and a pending assistant reply; returns the reply to run."""
    content = (text or "").strip()
    if not content:
        raise ChatError("Message is empty.")
    if len(content) > MAX_MESSAGE_CHARS:
        raise ChatError(f"Message is too long (max {MAX_MESSAGE_CHARS} characters).")
    with transaction.atomic():
        busy = (
            AgentMessage.objects.select_for_update()
            .filter(program=program, status=AgentMessage.Status.PENDING)
            .exists()
        )
        if busy:
            raise ChatError("I'm still answering your last message.")
        AgentMessage.objects.create(program=program, role=AgentMessage.Role.USER, content=content)
        return AgentMessage.objects.create(
            program=program, role=AgentMessage.Role.ASSISTANT, status=AgentMessage.Status.PENDING
        )
