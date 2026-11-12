"""
Outreach email channel: one task = one personalised pitch to a published contact.

Preparing: contact found -> not opted out -> not contacted in the last 90 days
-> pitch written -> rules + fact check. Nothing is sent.

Executing: sends exactly the approved subject and body from the brand's Gmail,
to the contact recorded when the draft was prepared, after re-checking that the
contact has not opted out in the meantime and that the mailbox is still the one
the draft was written for.

Contact emails are stored only in OutreachContact; drafts, outcomes and audit
entries carry the contact's id and the site domain, never the person's address.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from ...models import (
    ActionVersion,
    GrowthAction,
    GrowthProgram,
    GrowthTask,
    OutreachContact,
    OutreachThread,
)
from ..execution.brand import brand_facts, display_name
from ..execution.channel_base import ChannelOutcome, Prepared
from ..positioning.inputs import bare_domain
from .contacts import discover_contact
from .follow_up_actions import FOLLOW_UP_GAPS, execute_follow_up
from .gmail import GmailClient, GmailError
from .mailbox import gmail_integration, mailbox_address
from .pitch import check_pitch, with_footer, write_pitch

NOT_CONNECTED = "Connect Gmail so the agent can send outreach emails."
MAILBOX_CHANGED = "The connected mailbox changed after this draft was approved."
CONTACT_GONE = "The contact for this draft no longer exists."
SENDER_PLACEHOLDER = "your connected mailbox"
CONNECTOR = "gmail"
NEEDS_CONTACT = "contact"
UNVERIFIED = "The fact-check couldn't verify this pitch against the brand's facts."
RECONTACT_COOLDOWN = timedelta(days=90)
FIRST_FOLLOW_UP_AFTER = FOLLOW_UP_GAPS[0]
KNOWLEDGE_CHARS = 1500


def _contact_block(contact: OutreachContact) -> str:
    if contact.opted_out:
        return "This contact opted out of emails."
    recent = contact.last_contacted_at and timezone.now() - contact.last_contacted_at < RECONTACT_COOLDOWN
    return "This contact was emailed in the last 90 days." if recent else ""


def _not_sent(reason: str, **payload) -> ChannelOutcome:
    """A failure that is certain to have happened before anything was sent."""
    return ChannelOutcome(False, reason, payload=payload, retryable=False, external_call_made=False)


class OutreachEmailChannel:
    def readiness(self, program: GrowthProgram) -> str:
        """Drafting needs no mailbox: the owner can review pitches before connecting Gmail."""
        return ""

    def send_blocker(self, program: GrowthProgram) -> str:
        """Sending does: an approved email waits until Gmail is connected."""
        return "" if gmail_integration(program.organization) else NOT_CONNECTED

    def _contact(self, task: GrowthTask, domain: str) -> OutreachContact | None:
        """The owner's chosen contact if they gave one, otherwise one found on the site."""
        org = task.cycle.program.organization
        owner_contact_id = (task.result_payload or {}).get("owner_contact_id")
        if owner_contact_id:
            return OutreachContact.objects.filter(pk=owner_contact_id, organization=org).first()
        found = discover_contact(task.target_url, domain)
        if found is None:
            return None
        contact, _ = OutreachContact.objects.get_or_create(
            organization=org, email=found.email, defaults={"domain": domain, "source_url": found.source_url}
        )
        return contact

    def prepare(self, task: GrowthTask) -> Prepared | ChannelOutcome:
        org = task.cycle.program.organization
        domain = bare_domain(task.target_url) or bare_domain(task.target)
        contact = self._contact(task, domain)
        if contact is None:
            # Not a refusal: the owner may know a contact. They add one or skip.
            return ChannelOutcome(
                False,
                f"No published contact email found on {domain}.",
                payload={"domain": domain},
                retryable=False,
                external_call_made=False,
                needs_owner=NEEDS_CONTACT,
            )
        blocked = _contact_block(contact)
        if blocked:
            return _not_sent(blocked, domain=domain)

        knowledge = brand_facts(org, task.title, KNOWLEDGE_CHARS)
        pitch = write_pitch(task, task.target_url, knowledge)
        if pitch is None:
            return ChannelOutcome(False, "The writer model did not return a pitch.", external_call_made=False)
        verdict = check_pitch(pitch, display_name(org), knowledge)
        # The fact-check informs the owner; it doesn't decide for them.
        concerns = () if verdict.passed else (tuple(verdict.issues) or (UNVERIFIED,))
        sender = mailbox_address(gmail_integration(org))
        # The footer names the sender, so the stored body is the complete email.
        body = with_footer(pitch.body, sender or SENDER_PLACEHOLDER, display_name(org), org.url)
        return Prepared(
            kind=GrowthAction.Kind.SEND_OUTREACH_EMAIL,
            connector=CONNECTOR,
            payload={"subject": pitch.subject, "body": body},
            destination={"contact_id": contact.id, "domain": domain, "sender": sender},
            preview={"domain": domain, "subject": pitch.subject, "body": body},
            review_notes=concerns,
        )

    def execute(self, task: GrowthTask, version: ActionVersion) -> ChannelOutcome:
        if version.action.kind == GrowthAction.Kind.SEND_FOLLOW_UP:
            integration = gmail_integration(task.cycle.program.organization)
            if integration is None:
                return ChannelOutcome(False, NOT_CONNECTED, external_call_made=False)
            return execute_follow_up(task, version, GmailClient(integration))
        org = task.cycle.program.organization
        destination = version.destination
        domain = destination.get("domain", "")
        integration = gmail_integration(org)
        if integration is None:
            return ChannelOutcome(False, NOT_CONNECTED, external_call_made=False)
        if not destination.get("sender") or mailbox_address(integration) != destination["sender"]:
            return _not_sent(MAILBOX_CHANGED, domain=domain)
        contact = OutreachContact.objects.filter(pk=destination.get("contact_id"), organization=org).first()
        if contact is None:
            return _not_sent(CONTACT_GONE, domain=domain)
        blocked = _contact_block(contact)
        if blocked:
            return _not_sent(blocked, domain=domain)

        subject, body = version.payload["subject"], version.payload["body"]
        try:
            sent = GmailClient(integration).send(contact.email, subject, body)
        except GmailError as exc:
            # The send call was made. Gmail may have accepted the message even
            # though this failed, so the outcome is unknown, not "not sent".
            return ChannelOutcome(False, str(exc), payload={"domain": domain})
        now = timezone.now()
        OutreachThread.objects.create(
            task=task,
            contact=contact,
            gmail_thread_id=sent.thread_id,
            last_message_id=sent.rfc_message_id,
            subject=subject,
            next_follow_up_on=now.date() + FIRST_FOLLOW_UP_AFTER,
        )
        contact.last_contacted_at = now
        contact.save(update_fields=["last_contacted_at", "updated_at"])
        return ChannelOutcome(True, payload={"domain": domain, "subject": subject})
