"""
Outreach email channel: one task = one personalised pitch to a published contact.

Order of checks: contact found -> not opted out -> not contacted in the last
90 days -> pitch written -> rules + fact check -> send from the brand's Gmail.
Contact emails are stored only in OutreachContact; outcomes and audit entries
carry the site domain, never the person's address.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from apps.integrations.models import Integration

from ...models import ChannelMode, GrowthProgram, GrowthTask, OutreachContact, OutreachThread
from ..execution.brand import brand_facts, display_name
from ..execution.channel_base import ChannelOutcome
from ..positioning.inputs import bare_domain
from .contacts import discover_contact
from .gmail import GmailClient, GmailError
from .pitch import check_pitch, with_footer, write_pitch

NOT_CONNECTED = "Connect Gmail so the agent can send outreach emails."
RECONTACT_COOLDOWN = timedelta(days=90)
FIRST_FOLLOW_UP_AFTER = timedelta(days=4)
KNOWLEDGE_CHARS = 1500


def gmail_integration(organization) -> Integration | None:
    return (
        Integration.objects.filter(
            organization=organization, provider=Integration.Provider.GMAIL, is_active=True
        )
        .order_by("-updated_at")
        .first()
    )


def _contact_block(contact: OutreachContact) -> str:
    if contact.opted_out:
        return "This contact opted out of emails."
    recent = contact.last_contacted_at and timezone.now() - contact.last_contacted_at < RECONTACT_COOLDOWN
    return "This contact was emailed in the last 90 days." if recent else ""


class OutreachEmailChannel:
    def readiness(self, program: GrowthProgram) -> str:
        return "" if gmail_integration(program.organization) else NOT_CONNECTED

    def run(self, task: GrowthTask, mode: str) -> ChannelOutcome:
        org = task.cycle.program.organization
        domain = bare_domain(task.target_url) or bare_domain(task.target)
        found = discover_contact(task.target_url, domain)
        if found is None:
            return ChannelOutcome(False, f"No published contact email found on {domain}.", retryable=False)
        contact, _ = OutreachContact.objects.get_or_create(
            organization=org, email=found.email, defaults={"domain": domain, "source_url": found.source_url}
        )
        blocked = _contact_block(contact)
        if blocked:
            return ChannelOutcome(False, blocked, payload={"domain": domain}, retryable=False)

        knowledge = brand_facts(org, task.title, KNOWLEDGE_CHARS)
        pitch = write_pitch(task, task.target_url, knowledge)
        if pitch is None:
            return ChannelOutcome(False, "The writer model did not return a pitch.")
        verdict = check_pitch(pitch, display_name(org), knowledge)
        if not verdict.passed:
            return ChannelOutcome(
                False, "Blocked by brand-safety check.", payload={"issues": list(verdict.issues)}
            )
        if mode == ChannelMode.DRY_RUN:
            return ChannelOutcome(
                True, payload={"domain": domain, "subject": pitch.subject, "body": pitch.body}, dry_run=True
            )
        return self._send(task, contact, pitch, domain)

    def _send(self, task: GrowthTask, contact: OutreachContact, pitch, domain: str) -> ChannelOutcome:
        org = task.cycle.program.organization
        integration = gmail_integration(org)
        if integration is None:
            return ChannelOutcome(False, NOT_CONNECTED)
        sender = integration.metadata.get("email", "")
        try:
            sent = GmailClient(integration).send(
                contact.email, pitch.subject, with_footer(pitch.body, sender, display_name(org), org.url)
            )
        except GmailError as exc:
            return ChannelOutcome(False, str(exc))
        now = timezone.now()
        OutreachThread.objects.create(
            task=task,
            contact=contact,
            gmail_thread_id=sent.thread_id,
            last_message_id=sent.rfc_message_id,
            subject=pitch.subject,
            next_follow_up_on=now.date() + FIRST_FOLLOW_UP_AFTER,
        )
        contact.last_contacted_at = now
        contact.save(update_fields=["last_contacted_at", "updated_at"])
        return ChannelOutcome(True, payload={"domain": domain, "subject": pitch.subject})
