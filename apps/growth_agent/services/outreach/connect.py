"""
Gmail connection lifecycle for a brand: start OAuth, finish it, disconnect.

The OAuth ``state`` is signed (django.core.signing) and expires, and the
callback re-checks that the signed-in user still owns the brand before
storing any token.
"""

from __future__ import annotations

from django.core import signing
from django.db import transaction

from apps.integrations.models import Integration

from ...models import GrowthProgram
from ..control import safety
from .gmail import GmailClient, authorization_url, exchange_code
from .mailbox import gmail_integration, mailbox_address

STATE_SALT = "growth-agent-gmail-oauth"
STATE_MAX_AGE_SECONDS = 600


class ConnectError(Exception):
    """OAuth could not complete; the code is safe to put in a redirect URL."""


def start(program: GrowthProgram, email: str, return_url: str = "") -> str:
    """Google's consent page. ``return_url`` (an Agent mode page, already validated by
    the caller) is carried in the signed state so the callback can send the owner back."""
    data = {"program": program.id, "email": email}
    if return_url:
        data["return"] = return_url
    return authorization_url(signing.dumps(data, salt=STATE_SALT))


def return_url_of(state: str) -> str:
    """Where to send the browser after Google, or "" when the state carries none or is bad."""
    try:
        data = signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE_SECONDS)
    except signing.BadSignature:
        return ""
    return data.get("return", "") if isinstance(data, dict) else ""


def status(program: GrowthProgram) -> dict:
    integration = gmail_integration(program.organization)
    return {"connected": integration is not None, "mailbox": mailbox_address(integration)}


def _program_from_state(state: str) -> GrowthProgram:
    try:
        data = signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE_SECONDS)
    except signing.BadSignature as exc:  # includes SignatureExpired
        raise ConnectError("invalid_state") from exc
    program = (
        GrowthProgram.objects.select_related("organization")
        .filter(pk=data.get("program"), organization__owner_email__iexact=data.get("email", ""))
        .first()
    )
    if program is None:
        raise ConnectError("not_owner")
    return program


def finish(code: str, state: str) -> GrowthProgram:
    if not code:
        raise ConnectError("missing_code")
    program = _program_from_state(state)
    tokens = exchange_code(code)
    if not tokens.get("refresh_token"):
        raise ConnectError("no_refresh_token")
    sender = GmailClient(None, token=tokens["access_token"]).profile_email()
    with transaction.atomic():
        # The verified owner connected this mailbox: not an unknown-source change.
        safety.set_actor(safety.owner_actor(program.organization.owner_email))
        integration, _ = Integration.objects.update_or_create(
            organization=program.organization,
            provider=Integration.Provider.GMAIL,
            defaults={"is_active": True},
        )
        integration.set_access_token(tokens["access_token"])
        integration.set_refresh_token(tokens["refresh_token"])
        integration.metadata = {**integration.metadata, "email": sender.lower()}
        integration.save()
    return program


def disconnect(program: GrowthProgram) -> bool:
    with transaction.atomic():
        safety.set_actor(safety.owner_actor(program.organization.owner_email))
        updated = Integration.objects.filter(
            organization=program.organization, provider=Integration.Provider.GMAIL, is_active=True
        ).update(is_active=False)
    return updated > 0
