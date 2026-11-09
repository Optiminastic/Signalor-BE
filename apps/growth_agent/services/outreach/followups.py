"""
Autonomous reply detection and follow-ups for open outreach threads.

For each open thread: if anyone other than the sender has replied, stop (and
record a permanent opt-out if they said so). Otherwise, when a follow-up is
due, send the next short template in the same Gmail thread; after the last
one the thread closes. Paused or killed programs are left untouched.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from django.utils import timezone

from ...models import AgentActionLog, GrowthProgram, OutreachThread
from ..execution import audit
from ..execution.brand import display_name
from .channel import gmail_integration
from .gmail import GmailClient, GmailError
from .pitch import FOLLOW_UPS, follow_up_body, is_opt_out, with_footer

logger = logging.getLogger("apps")
SECOND_FOLLOW_UP_AFTER = timedelta(days=5)
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


def _send_follow_up(client: GmailClient, thread: OutreachThread, sender: str, today: date) -> None:
    org = thread.task.cycle.program.organization
    number = thread.followups_sent + 1
    name = display_name(org)
    body = with_footer(follow_up_body(number, name), sender, name, org.url)
    sent = client.send(
        thread.contact.email,
        f"Re: {thread.subject}",
        body,
        thread_id=thread.gmail_thread_id,
        in_reply_to=thread.last_message_id,
    )
    thread.followups_sent = number
    thread.last_message_id = sent.rfc_message_id or thread.last_message_id
    thread.status = OutreachThread.Status.FOLLOWED_UP
    thread.next_follow_up_on = today + SECOND_FOLLOW_UP_AFTER if number < len(FOLLOW_UPS) else None
    thread.save()
    audit.record(
        thread.task.cycle.program,
        thread.task,
        "follow_up",
        AgentActionLog.Outcome.SUCCEEDED,
        {"domain": thread.contact.domain, "number": number},
    )


def process_thread(client: GmailClient, thread: OutreachThread, sender: str, today: date) -> None:
    replies = [m for m in client.thread_messages(thread.gmail_thread_id) if m.sender and m.sender != sender]
    if replies:
        _record_reply(thread, opted_out=any(is_opt_out(m.snippet) for m in replies))
        return
    if thread.next_follow_up_on and thread.next_follow_up_on <= today:
        _send_follow_up(client, thread, sender, today)
    elif thread.next_follow_up_on is None and thread.followups_sent >= len(FOLLOW_UPS):
        thread.status = OutreachThread.Status.CLOSED
        thread.save(update_fields=["status", "updated_at"])


def process_open_threads(today: date | None = None) -> int:
    """Returns how many threads were processed. One Gmail client per brand."""
    today = today or timezone.now().date()
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
            process_thread(entry[0], thread, entry[1], today)
            processed += 1
        except GmailError:
            logger.warning("growth outreach: Gmail call failed for thread %s", thread.id)
    return processed
