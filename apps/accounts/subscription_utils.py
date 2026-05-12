"""
Subscription checks for paid features (e.g. GEO analysis).

- SUBSCRIPTION_REQUIRED=true → require an active subscription before starting
  analysis (see analysis_allowed_for_email).

- Plan caps (projects, tracked prompts, engines) use is_plan_limits_enforcement_enabled():
  off when DISABLE_PAYMENT=true, or ENFORCE_PLAN_LIMITS=false; on when
  ENFORCE_PLAN_LIMITS=true; otherwise on in production (DEBUG=False).
"""

from __future__ import annotations

import os

from django.conf import settings

from .models import Subscription, PLAN_LIMITS

# ── Internal / Free Emails ────────────────────────────────────────────────
INTERNAL_DOMAINS = {"optiminastic.com"}

# Specific addresses that get free unlimited access regardless of domain
# (e.g. founder/admin Gmail accounts used for testing the customer flow).
# Extra entries can be added via the INTERNAL_EMAILS env var (comma-separated).
INTERNAL_EMAILS = {"optiminastic@gmail.com"}


def _extra_internal_emails() -> set[str]:
    raw = os.environ.get("INTERNAL_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_internal_email(email: str | None) -> bool:
    """@optiminastic.com emails — and a small allowlist of specific addresses
    — get free unlimited access (business-tier limits, no payment required)."""
    raw = (email or "").strip().lower()
    if not raw or "@" not in raw:
        return False
    if raw in INTERNAL_EMAILS or raw in _extra_internal_emails():
        return True
    domain = raw.split("@", 1)[1]
    return domain in INTERNAL_DOMAINS


def _integration_subscription_required() -> bool:
    """
    Whether Shopify/WordPress OAuth must have an active active subscription.

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


def is_plan_limits_enforcement_enabled() -> bool:
    """
    Plan caps (projects, prompts, engines) — separate from SUBSCRIPTION_REQUIRED.

    - DISABLE_PAYMENT=true → off (local dev)
    - ENFORCE_PLAN_LIMITS=false → off
    - ENFORCE_PLAN_LIMITS=true → on
    - unset → on when DEBUG is False (production default)
    """
    if os.environ.get("DISABLE_PAYMENT", "").strip().lower() in ("1", "true", "yes"):
        return False
    raw = os.environ.get("ENFORCE_PLAN_LIMITS", "").strip().lower()
    if raw in ("0", "false", "no"):
        return False
    if raw in ("1", "true", "yes"):
        return True
    return not getattr(settings, "DEBUG", False)


def _upgrade_hint_for_plan(plan_key: str) -> str:
    """Next-step upgrade copy for projects, prompts, and engine limits."""
    if plan_key == "starter":
        return " Upgrade to Pro for a higher limit."
    if plan_key == "pro":
        return " Upgrade to Max for a higher limit."
    return " You are on the highest plan; contact support if you need more capacity."


def plan_limit_error_response_dict(message: str) -> dict:
    """Consistent API shape for 403 plan / quota responses."""
    return {
        "error": message,
        "code": "plan_limit_exceeded",
        "upgrade_required": True,
    }


def integration_connect_allowed_for_email(email: str | None) -> tuple[bool, str]:
    """
    Gate Shopify / WordPress OAuth on an active subscription.
    @optiminastic.com emails always allowed.
    """
    if is_internal_email(email):
        return True, ""

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
    @optiminastic.com emails always allowed.
    """
    if is_internal_email(email):
        return True, ""

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


# ── Plan Limit Helpers ────────────────────────────────────────────────────

def _get_sub(email: str | None) -> Subscription | None:
    raw = (email or "").strip().lower()
    if not raw:
        return None
    try:
        return Subscription.objects.get(email=raw)
    except Subscription.DoesNotExist:
        return None


def _effective_plan_key(email: str | None) -> str:
    if is_internal_email(email):
        return "business"
    sub = _get_sub(email)
    if sub and sub.is_active:
        return sub.plan
    return "starter"


def get_plan_limits(email: str | None) -> dict:
    """Return the plan limits dict for a user (defaults to starter).
    Internal emails get unlimited (business) limits."""
    if is_internal_email(email):
        return PLAN_LIMITS["business"]
    sub = _get_sub(email)
    if sub and sub.is_active:
        return sub.limits
    return PLAN_LIMITS["starter"]


def project_limit_reached(email: str | None) -> tuple[bool, str]:
    """Check if user has reached their project (organization) limit."""
    if is_internal_email(email):
        return False, ""
    if not is_plan_limits_enforcement_enabled():
        return False, ""

    em = (email or "").strip().lower()
    if not em:
        return True, "Email is required."

    limits = get_plan_limits(email)
    from apps.organizations.models import Organization

    count = Organization.objects.filter(owner_email=em).count()
    max_projects = limits["max_projects"]
    if count >= max_projects:
        pk = _effective_plan_key(email)
        return True, (
            f"Your {limits['label']} plan allows {max_projects} project(s)."
            f"{_upgrade_hint_for_plan(pk)}"
        )
    return False, ""


def prompt_limit_reached(email: str | None, run_id: int | None = None) -> tuple[bool, str]:
    """Check if user has reached their prompt tracking limit."""
    if is_internal_email(email):
        return False, ""
    if not is_plan_limits_enforcement_enabled():
        return False, ""

    em = (email or "").strip().lower()
    if not em:
        return True, "Email is required."

    limits = get_plan_limits(email)
    from apps.analyzer.models import PromptTrack

    count = PromptTrack.objects.filter(analysis_run__email=em).count()
    max_prompts = limits["max_prompts"]
    if count >= max_prompts:
        pk = _effective_plan_key(email)
        return True, (
            f"Your {limits['label']} plan allows {max_prompts} tracked prompts."
            f"{_upgrade_hint_for_plan(pk)}"
        )
    return False, ""


def prompt_batch_would_exceed(email: str | None, additional: int) -> tuple[bool, str]:
    """True if adding `additional` prompt rows would exceed the plan cap."""
    if is_internal_email(email):
        return False, ""
    if not is_plan_limits_enforcement_enabled():
        return False, ""

    em = (email or "").strip().lower()
    if not em:
        return True, "Email is required."

    limits = get_plan_limits(email)
    from apps.analyzer.models import PromptTrack

    count = PromptTrack.objects.filter(analysis_run__email=em).count()
    max_prompts = limits["max_prompts"]
    if count + additional > max_prompts:
        pk = _effective_plan_key(email)
        return True, (
            f"This run would exceed your {limits['label']} plan limit of {max_prompts} tracked prompts "
            f"(you have {count}, adding {additional})."
            f"{_upgrade_hint_for_plan(pk)}"
        )
    return False, ""


def engine_allowed(email: str | None, engine: str) -> tuple[bool, str]:
    """Check if the user's plan allows a specific AI engine (prompt / visibility)."""
    if is_internal_email(email):
        return True, ""
    if not is_plan_limits_enforcement_enabled():
        return True, ""

    eng = (engine or "").strip().lower()
    if not eng:
        return False, "Engine is required."

    limits = get_plan_limits(email)
    allowed = [e.lower() for e in limits["engines"]]
    if eng not in allowed:
        pk = _effective_plan_key(email)
        return False, (
            f"The {eng} engine is not included on your {limits['label']} plan."
            f"{_upgrade_hint_for_plan(pk)}"
        )
    return True, ""
