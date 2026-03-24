"""
Subscription checks for paid features (e.g. GEO analysis).

Default: enforcement is OFF. Set SUBSCRIPTION_REQUIRED=true to require an active
Stripe subscription before /api/analyzer/analyze/ (and related reanalyze paths).
"""

from __future__ import annotations

import os

from django.conf import settings

from .models import Subscription


def _integration_subscription_required() -> bool:
    """
    Whether Shopify/WordPress OAuth must have an active Stripe subscription.

    - DISABLE_PAYMENT=true → never enforce (local dev shortcut)
    - REQUIRE_SUBSCRIPTION_FOR_INTEGRATIONS=true  → always enforce
    - REQUIRE_SUBSCRIPTION_FOR_INTEGRATIONS=false → never enforce
    - unset → enforce only when DEBUG is False (production); allow on local DEBUG
    """
    if os.environ.get("DISABLE_PAYMENT", "").strip().lower() in ("1", "true", "yes"):
        return False
    raw = os.environ.get("REQUIRE_SUBSCRIPTION_FOR_INTEGRATIONS", "").strip().lower()
    if raw in ("0", "false", "no"):
        return False
    if raw in ("1", "true", "yes"):
        return True
    return not getattr(settings, "DEBUG", False)


def is_subscription_enforcement_enabled() -> bool:
    return os.environ.get("SUBSCRIPTION_REQUIRED", "false").lower() in (
        "1",
        "true",
        "yes",
    )


def integration_connect_allowed_for_email(email: str | None) -> tuple[bool, str]:
    """
    Gate Shopify / WordPress OAuth on an active Stripe subscription — same rule as
    GET /api/payments/status/ and the dashboard (not tied to SUBSCRIPTION_REQUIRED).

    Local dev: with DEBUG=True and env unset, connection is allowed without Stripe.
    Production: set REQUIRE_SUBSCRIPTION_FOR_INTEGRATIONS=true explicitly, or rely on
    default (enforced when DEBUG is False).
    """
    if not _integration_subscription_required():
        return True, ""

    raw = (email or "").strip()
    if not raw:
        return False, "Email is required."

    normalized = raw.lower()
    try:
        sub = Subscription.objects.get(email=normalized)
    except Subscription.DoesNotExist:
        return (
            False,
            "Active subscription required to connect your store.",
        )
    if not sub.is_active:
        return (
            False,
            "Active subscription required to connect your store.",
        )
    return True, ""


def analysis_allowed_for_email(email: str | None) -> tuple[bool, str]:
    """
    Returns (True, "") if this email may start analysis, else (False, error_message).
    """
    if not is_subscription_enforcement_enabled():
        return True, ""

    raw = (email or "").strip()
    if not raw:
        return False, "Email is required. Sign in to run analysis."

    normalized = raw.lower()
    try:
        sub = Subscription.objects.get(email=normalized)
    except Subscription.DoesNotExist:
        return (
            False,
            "Active subscription required. Complete checkout to run analysis.",
        )
    if not sub.is_active:
        return (
            False,
            "Your subscription is not active. Update billing to run analysis.",
        )
    return True, ""
