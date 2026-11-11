"""Observation of the brand edit/delete endpoint (SEC-1a-0).

``PATCH`` and ``DELETE /api/organizations/<id>/`` do not require sign-in yet.
Before that is enforced we measure who really calls them, and alert on the
calls that would be refused once it is.

For each request this module works out whether the caller WOULD be authorized
under the rules that will be enforced, without changing what the endpoint
returns. A verified token is identity, not authorization: a signed-in stranger
is classified exactly like an anonymous caller.

Nothing here may raise into the view or alter a response. Every entry point
catches its own failures.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail

from apps.accounts.agency_utils import writable_owner_emails
from core.auth.identity import verified_email

from .models import Organization

logger = logging.getLogger("apps")

OBSERVATION_EVENT = "org_detail_observation"
ALERT_EVENT = "org_detail_unauthorized_attempt"

METHOD_PATCH = "PATCH"
METHOD_DELETE = "DELETE"

CALLER_ANONYMOUS = "anonymous"
CALLER_INVALID_TOKEN = "invalid_token"
CALLER_VERIFIED = "verified"

VERDICT_YES = "yes"
VERDICT_NO = "no"
VERDICT_NOT_APPLICABLE = "n/a"
VERDICT_ERROR = "error"

ATTEMPT_DELETE = "delete"
ATTEMPT_OWNER_CHANGE = "owner_change"
ATTEMPT_CROSS_ACCOUNT_EDIT = "cross_account_edit"

OWNER_FIELD = "owner_email"
INVALID_ORIGIN = "invalid"
ALERT_EMAIL_COOLDOWN_SECONDS = 600
_SAFE_HOST = re.compile(r"^[a-z0-9.-]{1,253}$")
_UNSAFE_ORIGIN_CHARACTERS = re.compile(r"[\x00-\x20\x7f]")
_UNAUTHORIZED_VERDICTS = (VERDICT_NO, VERDICT_ERROR)


@dataclass(frozen=True)
class Observation:
    """What was seen about one request, before the operation ran."""

    method: str
    caller: str
    would_be_authorized: str
    owner_change_attempted: bool
    origin_host: str
    org_id: int | None

    def fields(self, status_code: int) -> str:
        """The only values that are ever logged or emailed. No token, email or body."""
        return (
            f"method={self.method} caller={self.caller} "
            f"would_be_authorized={self.would_be_authorized} "
            f"owner_change_attempted={'yes' if self.owner_change_attempted else 'no'} "
            f"origin_host={self.origin_host} status={status_code} org_id={self.org_id}"
        )


def would_be_authorized(method: str, email: str, org: Organization) -> bool:
    """Whether ``email`` would be allowed ``method`` on ``org`` once sign-in is enforced.

    Edit: the owner, or an admin of the agency that owns the brand.
    Delete: the owner only.
    """
    caller = (email or "").strip().lower()
    if not caller:
        return False
    owner = (org.owner_email or "").strip().lower()
    if method == METHOD_DELETE:
        return caller == owner
    return owner in writable_owner_emails(caller)


def owner_change_attempted(request) -> bool:
    data = getattr(request, "data", None)
    return isinstance(data, Mapping) and OWNER_FIELD in data


def _caller(request, email: str | None) -> str:
    if email:
        return CALLER_VERIFIED
    if request.META.get("HTTP_AUTHORIZATION"):
        return CALLER_INVALID_TOKEN
    return CALLER_ANONYMOUS


def _origin_host(request) -> str:
    """Host part of the Origin header. The header is caller-controlled, so anything
    that is not a plain hostname is replaced rather than written to the log."""
    origin = request.META.get("HTTP_ORIGIN") or ""
    if not origin:
        return ""
    # urlsplit silently drops tabs and newlines, which would turn a tampered
    # header into a different, believable hostname. Refuse those outright.
    if _UNSAFE_ORIGIN_CHARACTERS.search(origin):
        return INVALID_ORIGIN
    try:
        host = (urlsplit(origin).hostname or "").lower()
    except ValueError:
        return INVALID_ORIGIN
    return host if _SAFE_HOST.match(host) else INVALID_ORIGIN


def _verdict(method: str, email: str | None, org: Organization | None) -> str:
    if org is None:
        return VERDICT_NOT_APPLICABLE
    try:
        return VERDICT_YES if would_be_authorized(method, email or "", org) else VERDICT_NO
    except Exception:
        logger.exception("brand endpoint observation: authorization check failed")
        return VERDICT_ERROR


def observe(
    request, method: str, org: Organization | None, org_id: int | None, owner_change: bool
) -> Observation:
    """Classify the request. Call BEFORE the operation: after a delete the owner is gone.

    The request body is never read here. The view passes ``owner_change`` in, so a
    malformed body is parsed (and rejected) exactly where it always was.
    """
    try:
        email = verified_email(request)
        return Observation(
            method=method,
            caller=_caller(request, email),
            would_be_authorized=_verdict(method, email, org),
            owner_change_attempted=owner_change,
            origin_host=_origin_host(request),
            org_id=org_id,
        )
    except Exception:
        logger.exception("brand endpoint observation: could not classify the request")
        return Observation(method, CALLER_ANONYMOUS, VERDICT_ERROR, owner_change, "", org_id)


def alert_attempt(seen: Observation) -> str | None:
    """Which kind of unauthorized attempt this was, or None when no alert is due.

    A tokenless rename is deliberately not alerted: it is the legacy traffic the
    observation exists to count.
    """
    if seen.owner_change_attempted:
        return ATTEMPT_OWNER_CHANGE
    if seen.would_be_authorized not in _UNAUTHORIZED_VERDICTS:
        return None
    if seen.method == METHOD_DELETE:
        return ATTEMPT_DELETE
    if seen.caller == CALLER_VERIFIED:
        return ATTEMPT_CROSS_ACCOUNT_EDIT
    return None


def record(seen: Observation, status_code: int) -> None:
    """Write the observation line, and the alert when one is due. Never raises."""
    try:
        fields = seen.fields(status_code)
        logger.info("%s %s", OBSERVATION_EVENT, fields)
        attempt = alert_attempt(seen)
        if attempt is None:
            return
        # Error level is what reaches Sentry, the primary alert channel.
        logger.error("%s attempt=%s %s", ALERT_EVENT, attempt, fields)
        _email_alert(seen, attempt, fields)
    except Exception:
        logger.exception("brand endpoint observation: could not record the request")


def _email_alert(seen: Observation, attempt: str, fields: str) -> None:
    """Fallback channel: a direct email, at most one per brand and attempt per cooldown."""
    recipients = list(getattr(settings, "ORG_ENDPOINT_ALERT_EMAILS", None) or [])
    if not recipients or not _claim_email_slot(seen.org_id, attempt):
        return

    def send() -> None:
        try:
            send_mail(
                subject=f"[Signalor] Unauthorized brand endpoint attempt: {attempt}",
                message=f"{ALERT_EVENT} attempt={attempt} {fields}\n\nSee docs/runbooks/ORG_ENDPOINT_ALERTS.md",
                from_email=None,
                recipient_list=recipients,
            )
        except Exception:
            logger.exception("brand endpoint observation: alert email failed")

    _run_in_background(send)


def _claim_email_slot(org_id: int | None, attempt: str) -> bool:
    try:
        return bool(cache.add(f"org-endpoint-alert:{org_id}:{attempt}", 1, ALERT_EMAIL_COOLDOWN_SECONDS))
    except Exception:
        return True  # a cache outage must not silence the fallback channel


def _run_in_background(target: Callable[[], None]) -> None:
    """Keep the mail call off the request path so it can never slow the response."""
    threading.Thread(target=target, name="org-endpoint-alert", daemon=True).start()
