"""
The brand owner confirms a change to their own connector (mailbox, site,
repository) with a one-time code emailed to them (Phase 1 decision).

- The code is 8 digits from ``secrets``, valid 10 minutes, emailed to the owner
  address read from the database (never from the request).
- Only a keyed verifier is stored: HMAC-SHA256 with ``GROWTH_CONFIRMATION_HMAC_KEY``
  over the challenge, the change, the owner and both identities, and the code.
  Reading the database alone gives nothing to test guesses against.
- The owner must state the old and new identity exactly; they must match the
  recorded change.
- Limits: 3 codes per change per hour, 10 per brand per day, 5 attempts per
  code. A new code supersedes earlier ones. Each attempt is reserved and
  committed before the code is compared, and the database allows at most five,
  so concurrent guesses can't exceed the limit.
- Ownership changes and brand moves can't be confirmed here (superuser + TOTP),
  and nothing can be confirmed while ownership itself is in doubt.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.organizations.models import Organization

from ...models import GrowthProgram, GrowthSafetyChange, GrowthSafetyReview, SafetyConfirmationEvent
from ..execution.brand import display_name
from . import safety

Event = SafetyConfirmationEvent.Event
CODE_DIGITS = 8
CODE_LIFETIME = timedelta(minutes=10)
MAX_ATTEMPTS = 5
CODES_PER_CHANGE_PER_HOUR = 3
CODES_PER_BRAND_PER_DAY = 10
MIN_KEY_BYTES = 32
REFUSED = "That code or those details don't match. Check them and try again."


class ConfirmationError(Exception):
    """Refused; ``code`` and ``status`` are for the API, the message is safe to show."""

    def __init__(self, code: str, message: str, status: int = 400, attempts_left: int | None = None):
        super().__init__(message)
        self.code, self.status, self.attempts_left = code, status, attempts_left


@dataclass(frozen=True)
class ConfirmRequest:
    code: str
    old_identity: str
    new_identity: str
    reason: str
    owner_email: str


def _key() -> bytes:
    key = (getattr(settings, "GROWTH_CONFIRMATION_HMAC_KEY", "") or "").encode()
    if len(key) < MIN_KEY_BYTES:
        raise ConfirmationError("not_configured", "Confirmation codes aren't set up on the server yet.", 503)
    return key


def _verifier(key: bytes, challenge_id: str, change: GrowthSafetyChange, owner: str, code: str) -> str:
    message = "|".join([challenge_id, str(change.pk), owner, change.old_digest, change.new_digest, code])
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()


def confirmable(program: GrowthProgram, change_id: int) -> GrowthSafetyChange:
    """The change, if this owner may confirm it now; else raise."""
    change = safety.unresolved(program.organization_id).filter(pk=change_id).first()
    if change is None or not safety.owner_may_confirm(change):
        raise ConfirmationError("not_found", "No such change to confirm.", 404)
    if safety.ownership_in_doubt(program.organization_id):
        raise ConfirmationError(
            "ownership_in_doubt",
            "This brand's ownership is under review; connector changes wait for that.",
            409,
        )
    return change


def _issued(change: GrowthSafetyChange):
    return SafetyConfirmationEvent.objects.filter(change=change, event=Event.ISSUED)


def _check_limits(change: GrowthSafetyChange) -> None:
    now = timezone.now()
    if _issued(change).filter(created_at__gte=now - timedelta(hours=1)).count() >= CODES_PER_CHANGE_PER_HOUR:
        raise ConfirmationError(
            "too_many_codes", "Too many codes for this change. Try again in an hour.", 429
        )
    brand_codes = SafetyConfirmationEvent.objects.filter(
        organization_id=change.organization_id, event=Event.ISSUED, created_at__gte=now - timedelta(days=1)
    )
    if brand_codes.count() >= CODES_PER_BRAND_PER_DAY:
        raise ConfirmationError("too_many_codes", "Too many codes today. Try again tomorrow.", 429)


def _supersede_live(change: GrowthSafetyChange) -> None:
    closed = SafetyConfirmationEvent.objects.filter(
        change=change, event__in=[Event.VERIFIED, Event.SUPERSEDED]
    ).values_list("challenge_id", flat=True)
    for challenge_id in (
        _issued(change).exclude(challenge_id__in=list(closed)).values_list("challenge_id", flat=True)
    ):
        SafetyConfirmationEvent.objects.create(
            organization_id=change.organization_id,
            change=change,
            challenge_id=challenge_id,
            event=Event.SUPERSEDED,
        )


def _email(org: Organization, change: GrowthSafetyChange, code: str) -> None:
    what = f"{change.provider or change.entity} {change.field.replace('metadata.', '')}"
    body = (
        f'Someone changed {display_name(org)}\'s {what} from "{change.old_display}" to "{change.new_display}".\n\n'
        f"If this was you, confirm it in Signalor with this code: {code}\n"
        f"It works for 10 minutes. If this wasn't you, don't use the code and contact support.\n"
    )
    send_mail(f"Confirm a change to {display_name(org)}", body, None, [org.owner_email])


def issue(program: GrowthProgram, change_id: int) -> dict:
    """Email a fresh code for this change to the brand owner. Returns when it expires."""
    key = _key()
    org = program.organization
    with transaction.atomic():
        Organization.objects.select_for_update().filter(pk=org.pk).exists()
        change = confirmable(program, change_id)
        _check_limits(change)
        _supersede_live(change)
        code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
        challenge_id = secrets.token_hex(16)
        expires = timezone.now() + CODE_LIFETIME
        SafetyConfirmationEvent.objects.create(
            organization_id=org.pk,
            change=change,
            challenge_id=challenge_id,
            event=Event.ISSUED,
            owner_digest=safety.owner_digest(org.owner_email),
            verifier=_verifier(key, challenge_id, change, safety.owner_digest(org.owner_email), code),
            key_id=getattr(settings, "GROWTH_CONFIRMATION_KEY_ID", "k1"),
            expires_at=expires,
        )
        transaction.on_commit(lambda: _email(org, change, code))
    return {"expires_at": expires.isoformat()}


def _live_challenge(change: GrowthSafetyChange) -> SafetyConfirmationEvent | None:
    issued = _issued(change).order_by("-created_at").first()
    if issued is None or issued.expires_at < timezone.now():
        return None
    closed = SafetyConfirmationEvent.objects.filter(
        challenge_id=issued.challenge_id, event__in=[Event.VERIFIED, Event.SUPERSEDED]
    )
    return None if closed.exists() else issued


def _reserve(
    program: GrowthProgram, change_id: int
) -> tuple[GrowthSafetyChange, SafetyConfirmationEvent, int]:
    """Transaction A: claim the next attempt number, committed before anything is compared."""
    try:
        with transaction.atomic():
            Organization.objects.select_for_update().filter(pk=program.organization_id).exists()
            change = confirmable(program, change_id)
            challenge = _live_challenge(change)
            if challenge is None:
                raise ConfirmationError(
                    "no_code", "There's no valid code for this change. Ask for a new one."
                )
            used = SafetyConfirmationEvent.objects.filter(
                challenge_id=challenge.challenge_id, event=Event.ATTEMPT
            ).count()
            if used >= MAX_ATTEMPTS:
                raise ConfirmationError(
                    "no_attempts_left", "That code has no attempts left. Ask for a new one."
                )
            SafetyConfirmationEvent.objects.create(
                organization_id=change.organization_id,
                change=change,
                challenge_id=challenge.challenge_id,
                event=Event.ATTEMPT,
                attempt_no=used + 1,
            )
            return change, challenge, used + 1
    except IntegrityError as exc:  # another attempt took that number, or the fifth was already used
        raise ConfirmationError("no_attempts_left", REFUSED) from exc


def _matches(change: GrowthSafetyChange, old: str, new: str) -> bool:
    def same(value: str, recorded: str) -> bool:
        value = value.strip()
        return safety.digest(value) == recorded or safety.digest(value.lower()) == recorded

    return same(old, change.old_digest) and same(new, change.new_digest)


def _still_latest(change: GrowthSafetyChange) -> bool:
    newer = GrowthSafetyChange.objects.filter(
        organization_id=change.organization_id,
        entity=change.entity,
        entity_id=change.entity_id,
        field=change.field,
        pk__gt=change.pk,
    )
    return not newer.exists()


def _evaluate(program, change, challenge, attempt_no: int, request: ConfirmRequest) -> str:
    """Transaction B: compare the code and identities; record the result."""
    with transaction.atomic():
        Organization.objects.select_for_update().filter(pk=program.organization_id).exists()
        owner = safety.owner_digest(program.organization.owner_email)
        if safety.owner_digest(request.owner_email) != owner or safety.ownership_in_doubt(
            program.organization_id
        ):
            outcome = "refused"
        elif not _still_latest(change):
            outcome = "refused"
        elif not _matches(change, request.old_identity, request.new_identity):
            outcome = "identity_mismatch"
        else:
            expected = _verifier(_key(), challenge.challenge_id, change, owner, request.code.strip())
            outcome = "verified" if hmac.compare_digest(expected, challenge.verifier) else "wrong_code"
        SafetyConfirmationEvent.objects.create(
            organization_id=change.organization_id,
            change=change,
            challenge_id=challenge.challenge_id,
            event=Event.RESULT,
            attempt_no=attempt_no,
            outcome=outcome,
        )
        if outcome == "verified":
            SafetyConfirmationEvent.objects.create(
                organization_id=change.organization_id,
                change=change,
                challenge_id=challenge.challenge_id,
                event=Event.VERIFIED,
                owner_digest=owner,
            )
            safety.record_review(
                change,
                GrowthSafetyReview.Outcome.CLEARED,
                step_up=GrowthSafetyReview.StepUp.EMAIL_CODE,
                reason=request.reason or "Confirmed by the brand owner with an emailed code.",
                reviewer_owner_digest=owner,
            )
    return outcome


def confirm(program: GrowthProgram, change_id: int, request: ConfirmRequest) -> GrowthSafetyChange:
    """Clear the change if the code and both identities are right; otherwise raise."""
    if not request.reason.strip():
        raise ConfirmationError("reason_required", "Say in a few words why this change is expected.")
    change, challenge, attempt_no = _reserve(program, change_id)
    outcome = _evaluate(program, change, challenge, attempt_no, request)
    if outcome != "verified":
        raise ConfirmationError("refused", REFUSED, attempts_left=MAX_ATTEMPTS - attempt_no)
    return change
