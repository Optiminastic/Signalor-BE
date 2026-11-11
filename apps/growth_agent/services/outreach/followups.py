"""
Reply detection for open outreach threads.

For each open thread: if anyone other than the sender has replied, the thread
stops (and a permanent opt-out is recorded if they said so). Paused or killed
programs are left untouched.

Follow-up emails are NOT sent from here. Every email the agent sends needs the
brand owner's approval, and a follow-up is its own email. Until follow-ups are
prepared and approved like any other action, a thread whose follow-up is due
simply stays open with its due date, so nothing is lost and nothing goes out
unapproved.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from ...models import AgentActionLog, GrowthProgram, OutreachThread
from ..execution import audit
from .channel import gmail_integration
from .gmail import GmailClient, GmailError
from .pitch import is_opt_out

logger = logging.getLogger("apps")
OPEN_STATUSES = (OutreachThread.Status.SENT, OutreachThread.Status.FOLLOWED_UP)


def open_threads():
    return OutreachThread.objects.filter(
        status__in=OPEN_STATUSES,
        task__cycle__program__status=GrowthProgram.Status.ACTIVE,
        task__cycle__program__kill_switch=False,
    ).select_related("contact", "task__cycle__program__organization")


def _record_reply(thread: OutreachThread, opted_out: bool) -> None:
    thread.status = OutreachThread.Status.OPTED_OUT if opted_out else OutreachThread.Status.REPLIED
    thread.replied_at = timezone.now()
    thread.next_follow_up_on = None
    thread.save(update_fields=["status", "replied_at", "next_follow_up_on", "updated_at"])
    if opted_out:
        thread.contact.opted_out = True
        thread.contact.save(update_fields=["opted_out", "updated_at"])
    action = "opt_out_received" if opted_out else "reply_received"
    audit.record(
        thread.task.cycle.program,
        thread.task,
        action,
        AgentActionLog.Outcome.SUCCEEDED,
        {"domain": thread.contact.domain},
    )


def process_thread(client: GmailClient, thread: OutreachThread, sender: str) -> None:
    replies = [m for m in client.thread_messages(thread.gmail_thread_id) if m.sender and m.sender != sender]
    if replies:
        _record_reply(thread, opted_out=any(is_opt_out(m.snippet) for m in replies))


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
