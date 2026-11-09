"""
Service authentication for the standalone ``ai-agent-signalor`` frontend.

The browser never calls these endpoints. The frontend's Next.js server checks
the user's better-auth session (same Signalor accounts), then signs each
request with a shared secret that only that server holds:

    X-Growth-Email:     the verified user email
    X-Growth-Timestamp: unix seconds
    X-Growth-Signature: hex HMAC-SHA256(secret, "<ts>.<email>.<METHOD>.<path>")

Signing (instead of sending the raw secret) means a captured request can't be
replayed after the skew window or re-targeted at another endpoint/method.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission

from apps.accounts.subscription_utils import (
    effective_plan_key,
    is_plan_limits_enforcement_enabled,
)

EMAIL_HEADER = "HTTP_X_GROWTH_EMAIL"
TIMESTAMP_HEADER = "HTTP_X_GROWTH_TIMESTAMP"
SIGNATURE_HEADER = "HTTP_X_GROWTH_SIGNATURE"
MAX_CLOCK_SKEW_SECONDS = 300
ELIGIBLE_PLANS = frozenset({"pro", "business"})


@dataclass(frozen=True)
class GrowthPrincipal:
    """The verified caller. Exposes what DRF auth and throttling expect."""

    email: str

    @property
    def pk(self) -> str:
        # UserRateThrottle keys on user.pk; the email is unique per account.
        return f"growth:{self.email}"

    @property
    def is_authenticated(self) -> bool:
        return True


def sign_request(secret: str, timestamp: str, email: str, method: str, path: str) -> str:
    message = f"{timestamp}.{email}.{method.upper()}.{path}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _check_timestamp(raw: str) -> None:
    try:
        sent_at = int(raw)
    except (TypeError, ValueError) as exc:
        raise AuthenticationFailed("Invalid request timestamp.") from exc
    if abs(time.time() - sent_at) > MAX_CLOCK_SKEW_SECONDS:
        raise AuthenticationFailed("Request timestamp outside the allowed window.")


class GrowthServiceAuthentication(BaseAuthentication):
    def authenticate(self, request):
        secret = getattr(settings, "GROWTH_AGENT_SIGNING_SECRET", "")
        if not secret:
            raise AuthenticationFailed("Growth Agent is not configured.")
        meta = request.META
        email = (meta.get(EMAIL_HEADER) or "").strip().lower()
        timestamp = meta.get(TIMESTAMP_HEADER) or ""
        signature = meta.get(SIGNATURE_HEADER) or ""
        if not email or "@" not in email or not signature:
            raise AuthenticationFailed("Missing Growth Agent credentials.")
        _check_timestamp(timestamp)
        expected = sign_request(secret, timestamp, email, request.method, request.path)
        if not hmac.compare_digest(expected, signature):
            raise AuthenticationFailed("Invalid Growth Agent signature.")
        return GrowthPrincipal(email=email), None

    def authenticate_header(self, request) -> str:
        return "GrowthSignature"


def has_growth_access(email: str) -> bool:
    """Pro and Max (``business``) plans only; internal emails resolve to business."""
    if not is_plan_limits_enforcement_enabled():
        return True
    return effective_plan_key(email) in ELIGIBLE_PLANS


class HasGrowthAgentPlan(BasePermission):
    message = "The Growth Agent is available on the Pro and Max plans."

    def has_permission(self, request, view) -> bool:
        user = request.user
        return isinstance(user, GrowthPrincipal) and has_growth_access(user.email)
