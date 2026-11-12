"""
Follow-up emails as their own approved actions.

A follow-up is a separate email, so it gets its own draft and its own owner
approval, exactly like the first email:

- ``prepare_due_follow_ups``: for each open thread whose follow-up is due,
  draft the next step as a ``send_follow_up`` action (one per step; a database
  constraint enforces it). Nothing is sent.
- ``follow_up_block``: the final check before sending. The thread must still
  be open, the contact not opted out, and no other email sent in the thread
  since the draft was made.
- ``execute_follow_up``: just before sending, read the Gmail thread live; if
  the contact has replied, record the reply and send nothing.
- ``record_reply``: a reply closes the thread and stops any pending follow-up.

The sequence ends when a step is rejected, expires or is stopped, because the
next step is only drafted after the previous one was sent.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from ...models import (
    ActionVersion,
    AgentActionLog,
    ApprovalDecision,
    GrowthAction,
    GrowthCycle,
    GrowthProgram,
    GrowthTask,
    OutreachContact,
    OutreachThread,
)
from ..control import actions
from ..execution import audit
from ..execution.brand import display_name
from ..execution.channel_base import ChannelOutcome, Prepared
from .gmail import GmailClient, GmailError
from .mailbox import gmail_integration, mailbox_address
from .pitch import FOLLOW_UPS, follow_up_body, is_opt_out, with_footer

CONNECTOR = "gmail"
OPEN_STATUSES = (OutreachThread.Status.SENT, OutreachThread.Status.FOLLOWED_UP)
# Days to wait for a reply: after the first email, then after each follow-up.
FOLLOW_UP_GAPS = (timedelta(days=4), timedelta(days=5))
PENDING = (GrowthAction.State.AWAITING_APPROVAL, GrowthAction.State.APPROVED)
ROLE_SYSTEM = "system"
REPLIED_NOTE = "The contact replied, so this follow-up isn't needed."

THREAD_GONE = "The outreach thread for this follow-up no longer exists."
THREAD_CLOSED = "The thread is no longer open (they replied, opted out or it was closed)."
OPTED_OUT = "This contact opted out of emails."
THREAD_MOVED_ON = "Another email was sent in this thread after this follow-up was drafted."
OUT_OF_ORDER = "This follow-up is out of order for its thread."
REPLIED_FIRST = "The contact replied before the follow-up was sent, so it was not sent."
MAILBOX_CHANGED = "The connected mailbox changed after this follow-up was drafted."


def _reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def due_threads(today):
    return OutreachThread.objects.filter(
        status__in=OPEN_STATUSES,
        next_follow_up_on__lte=today,
        followups_sent__lt=len(FOLLOW_UPS),
        contact__opted_out=False,
        task__cycle__status=GrowthCycle.Status.ACTIVE,
        task__cycle__program__status=GrowthProgram.Status.ACTIVE,
        task__cycle__program__kill_switch=False,
    ).select_related("contact", "task__cycle__program__organization")


def _prepared(thread: OutreachThread, sequence: int, sender: str) -> Prepared:
    org = thread.task.cycle.program.organization
    brand = display_name(org)
    body = with_footer(follow_up_body(sequence, brand), sender, brand, org.url)
    payload = {"subject": _reply_subject(thread.subject), "body": body}
    destination = {
        "thread_id": thread.pk,
        "gmail_thread_id": thread.gmail_thread_id,
        "in_reply_to": thread.last_message_id,
        "contact_id": thread.contact_id,
        "domain": thread.contact.domain,
        "sender": sender,
        "sequence": sequence,
    }
    return Prepared(GrowthAction.Kind.SEND_FOLLOW_UP, CONNECTOR, payload, destination, preview={})


def _draft(thread: OutreachThread) -> bool:
    sequence = thread.followups_sent + 1
    if thread.follow_up_actions.filter(sequence_no=sequence).exists():
        return False
    sender = mailbox_address(gmail_integration(thread.task.cycle.program.organization))
    if not sender:
        return False  # no mailbox to send from; drafting would only create a dead end
    try:
        with transaction.atomic():
            actions.create_action(
                thread.task, _prepared(thread, sequence, sender), thread=thread, sequence_no=sequence
            )
    except IntegrityError:
        return False  # another round drafted this step first
    return True


def prepare_due_follow_ups(today) -> int:
    """Draft the next follow-up for every thread where one is due. Returns how many."""
    return sum(_draft(thread) for thread in due_threads(today))


def follow_up_block(action: GrowthAction) -> str:
    """Why this follow-up must not be sent as it stands, or "" if it may be."""
    thread = action.thread
    destination = action.current_version.destination
    if thread is None:
        return THREAD_GONE
    if thread.status not in OPEN_STATUSES:
        return THREAD_CLOSED
    if thread.contact.opted_out:
        return OPTED_OUT
    if thread.last_message_id != destination.get("in_reply_to"):
        return THREAD_MOVED_ON
    if thread.followups_sent + 1 != destination.get("sequence"):
        return OUT_OF_ORDER
    return ""


def _stop(action: GrowthAction) -> None:
    ApprovalDecision.objects.create(
        organization=action.organization,
        action=action,
        version=action.current_version,
        payload_hash=action.current_version.payload_hash,
        decision=ApprovalDecision.Decision.STOPPED,
        actor_email="",
        actor_role=ROLE_SYSTEM,
        reason=REPLIED_NOTE,
    )
    action.state = GrowthAction.State.STOPPED
    action.save(update_fields=["state", "updated_at"])


def stop_pending_follow_ups(thread: OutreachThread) -> int:
    """Stop every follow-up for ``thread`` that hasn't started sending."""
    stopped = 0
    for action_id in thread.follow_up_actions.filter(state__in=PENDING).values_list("id", flat=True):
        with transaction.atomic():
            action = actions.lock_actions().select_related("current_version").filter(pk=action_id).first()
            if action is None or action.state not in PENDING:
                continue
            _stop(action)
            stopped += 1
    return stopped


def record_reply(thread: OutreachThread, opted_out: bool) -> None:
    """Close the thread on a reply (or opt-out) and stop pending follow-ups."""
    thread.status = OutreachThread.Status.OPTED_OUT if opted_out else OutreachThread.Status.REPLIED
    thread.replied_at = timezone.now()
    thread.next_follow_up_on = None
    thread.save(update_fields=["status", "replied_at", "next_follow_up_on", "updated_at"])
    if opted_out:
        thread.contact.opted_out = True
        thread.contact.save(update_fields=["opted_out", "updated_at"])
    stop_pending_follow_ups(thread)
    action = "opt_out_received" if opted_out else "reply_received"
    audit.record(
        thread.task.cycle.program,
        thread.task,
        action,
        AgentActionLog.Outcome.SUCCEEDED,
        {"domain": thread.contact.domain},
    )


def _not_sent(reason: str, domain: str) -> ChannelOutcome:
    return ChannelOutcome(
        False, reason, payload={"domain": domain}, retryable=False, external_call_made=False
    )


def _advance(thread: OutreachThread, rfc_message_id: str) -> None:
    sent = thread.followups_sent + 1
    thread.followups_sent = sent
    thread.last_message_id = rfc_message_id or thread.last_message_id
    thread.status = OutreachThread.Status.FOLLOWED_UP
    thread.next_follow_up_on = (
        timezone.now().date() + FOLLOW_UP_GAPS[sent] if sent < len(FOLLOW_UPS) else None
    )
    thread.save(
        update_fields=["followups_sent", "last_message_id", "status", "next_follow_up_on", "updated_at"]
    )


def execute_follow_up(task: GrowthTask, version: ActionVersion, client: GmailClient) -> ChannelOutcome:
    """Send an approved follow-up, after a live check of the thread for replies."""
    destination = version.destination
    domain = destination.get("domain", "")
    org = task.cycle.program.organization
    if mailbox_address(gmail_integration(org)) != destination.get("sender"):
        return _not_sent(MAILBOX_CHANGED, domain)
    thread = (
        OutreachThread.objects.filter(pk=destination.get("thread_id"), task=task)
        .select_related("contact")
        .first()
    )
    contact = OutreachContact.objects.filter(pk=destination.get("contact_id"), organization=org).first()
    if thread is None or contact is None:
        return _not_sent(THREAD_GONE, domain)
    if contact.opted_out:
        return _not_sent(OPTED_OUT, domain)
    try:
        messages = client.thread_messages(destination["gmail_thread_id"])
    except GmailError as exc:
        return _not_sent(f"Couldn't check the thread for replies before sending: {exc}", domain)
    sender = destination["sender"].lower()
    replies = [m for m in messages if m.sender and m.sender.lower() != sender]
    if replies:
        record_reply(thread, opted_out=any(is_opt_out(m.snippet) for m in replies))
        return _not_sent(REPLIED_FIRST, domain)
    subject, body = version.payload["subject"], version.payload["body"]
    try:
        sent = client.send(
            contact.email,
            subject,
            body,
            thread_id=destination["gmail_thread_id"],
            in_reply_to=destination["in_reply_to"],
        )
    except GmailError as exc:
        # The send call was made; Gmail may have accepted it. Outcome unknown.
        return ChannelOutcome(False, str(exc), payload={"domain": domain})
    _advance(thread, sent.rfc_message_id)
    contact.last_contacted_at = timezone.now()
    contact.save(update_fields=["last_contacted_at", "updated_at"])
    return ChannelOutcome(
        True, payload={"domain": domain, "subject": subject, "sequence": destination["sequence"]}
    )
