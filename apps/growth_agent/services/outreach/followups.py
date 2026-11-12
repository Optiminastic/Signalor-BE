"""
Reply detection for open outreach threads.

For each open thread: if anyone other than the sender has replied, the thread
stops (and a permanent opt-out is recorded if they said so). Paused or killed
programs are left untouched.

Follow-up emails are NOT sent from here. A follow-up is its own email with its
own approval: it is drafted by ``follow_up_actions.prepare_due_follow_ups`` and
sent only once the owner approves it. A reply recorded here stops any pending
follow-up for the thread.
"""

from __future__ import annotations

import logging

from ...models import GrowthProgram, OutreachThread
from .follow_up_actions import FOLLOW_UP_GAPS, OPEN_STATUSES, record_reply
from .gmail import GmailClient, GmailError
from .mailbox import gmail_integration
from .pitch import is_opt_out

logger = logging.getLogger("apps")
__all__ = ["FOLLOW_UP_GAPS", "OPEN_STATUSES", "open_threads", "process_open_threads", "process_thread"]


def open_threads():
    return OutreachThread.objects.filter(
        status__in=OPEN_STATUSES,
        task__cycle__program__status=GrowthProgram.Status.ACTIVE,
        task__cycle__program__kill_switch=False,
    ).select_related("contact", "task__cycle__program__organization")


def process_thread(client: GmailClient, thread: OutreachThread, sender: str) -> None:
    replies = [m for m in client.thread_messages(thread.gmail_thread_id) if m.sender and m.sender != sender]
    if replies:
        record_reply(thread, opted_out=any(is_opt_out(m.snippet) for m in replies))


def process_open_threads() -> int:
    """Returns how many threads were processed. One Gmail client per brand."""
    clients: dict[int, tuple[GmailClient, str] | None] = {}
    processed = 0
    for thread in open_threads():
        org = thread.task.cycle.program.organization
        if org.id not in clients:
            integration = gmail_integration(org)
            clients[org.id] = (
                (GmailClient(integration), integration.metadata.get("email", "").lower())
                if integration
                else None
            )
        entry = clients[org.id]
        if entry is None:
            continue
        try:
            process_thread(entry[0], thread, entry[1])
            processed += 1
        except GmailError:
            logger.warning("growth outreach: Gmail call failed for thread %s", thread.id)
    return processed
