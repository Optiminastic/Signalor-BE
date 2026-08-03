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
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models import (
    AGENCY_MAX_PROJECTS,
    AGENCY_UNPAID_MAX_PROJECTS,
    PLAN_LIMITS,
    AccountProfile,
    Subscription,
)

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


# Free / personal email providers that are NOT allowed for Agency accounts.
# Keep in sync with the frontend list in `src/lib/work-email.ts`.
FREE_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "outlook.co.uk",
    "hotmail.com",
    "hotmail.co.uk",
    "live.com",
    "live.co.uk",
    "msn.com",
    "yahoo.com",
    "yahoo.co.uk",
    "yahoo.in",
    "ymail.com",
    "rocketmail.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "proton.me",
    "protonmail.com",
    "pm.me",
    "aol.com",
    "gmx.com",
    "gmx.net",
    "mail.com",
    "zoho.com",
    "yandex.com",
    "yandex.ru",
    "tutanota.com",
    "tuta.io",
    "hey.com",
    "fastmail.com",
    "hushmail.com",
    "inbox.com",
}


def is_free_email(email: str | None) -> bool:
    """True if the email is from a known free/personal provider (not a work
    address). Used to gate Agency accounts, which require a company email."""
    raw = (email or "").strip().lower()
    if not raw or "@" not in raw:
        return False
    return raw.rsplit("@", 1)[1] in FREE_EMAIL_DOMAINS


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
        return " Upgrade to Managed Growth for more prompts and hands-on support."
    if plan_key == "pro":
        return " Contact sales for an Enterprise plan with higher limits."
    return " You are on the highest plan; contact sales if you need more capacity."


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


# ── Account Type (Individual / Brand vs Agency) ───────────────────────────


def get_account_type(email: str | None) -> str:
    """Server-derived account type. Absent row → 'individual'.

    Account type is ALWAYS resolved here from the AccountProfile row, never
    from a client-supplied request field — enforcement must not trust the
    caller's claim (see CLAUDE.md §5.3).
    """
    raw = (email or "").strip().lower()
    if not raw:
        return "individual"
    row = AccountProfile.objects.filter(email=raw).only("account_type").first()
    return row.account_type if row else "individual"


def is_agency(email: str | None) -> bool:
    return get_account_type(email) == "agency"


def effective_max_projects(email: str | None) -> int:
    """max_projects after applying account type and plan.

    A brand IS a project, and paying Individual accounts are single-brand by
    design — so they are capped at exactly one, regardless of plan. Agencies are
    the multi-brand tier: this is the single seam that unlocks multiple projects
    for them.

    Agency capacity is derived from the plan, NOT from a flat constant. Two
    rules matter, and both used to be missing:

      - An agency with no ACTIVE subscription gets AGENCY_UNPAID_MAX_PROJECTS.
        ``get_plan_limits`` falls back to starter limits for an account with no
        Subscription row, so keying off the plan alone silently handed a full
        agency allowance to accounts that had never paid.
      - A paying agency gets its plan's ``max_agency_projects`` (0 = uncapped,
        for grandfathered "business" rows).

    Internal accounts are exempt from all of it. ``is_internal_email`` grants
    business-tier access with no payment, and the team's own workspaces hold
    several test/demo brands — a hard cap of one there is the "Max (Internal)"
    account being unable to add a second brand.
    """
    if is_internal_email(email):
        return AGENCY_MAX_PROJECTS
    if not is_agency(email):
        return 1

    sub = _get_sub(email)
    if not (sub and sub.is_active):
        return AGENCY_UNPAID_MAX_PROJECTS

    limits = get_plan_limits(email)
    allowance = int(limits.get("max_agency_projects", 0) or 0)
    if allowance <= 0:  # 0 = uncapped (grandfathered / internal-tier plans)
        return AGENCY_MAX_PROJECTS
    # Never below the plan's own individual base, so an agency can't end up with
    # fewer brands than the same plan grants an individual.
    return max(limits["max_projects"], allowance)


def _tracked_prompt_count(email: str) -> int:
    """Number of tracked prompts that consume this email's plan quota.

    Two deliberate scoping rules so a low-prompt plan (e.g. 10) isn't a
    one-run-ever trap:
      - Exclude soft-deleted prompts (``deleted_at`` set) — deleting a prompt
        frees its slot.
      - Scope to the current billing period when known (``current_period_end``),
        so re-analysis in a new cycle doesn't permanently consume the cap. This
        matches the billing UI's "counts reset on your next billing date" copy.
        Users without an active subscription (no period) fall back to an
        all-time count of their non-deleted prompts.
    """
    from apps.analyzer.models import PromptTrack

    qs = PromptTrack.objects.filter(analysis_run__email=email, deleted_at__isnull=True)
    sub = _get_sub(email)
    if sub and sub.current_period_end:
        # Monthly cycles: the current period began ~1 month before its end.
        # 31 days is intentionally generous at month boundaries.
        period_start = sub.current_period_end - timedelta(days=31)
        qs = qs.filter(created_at__gte=period_start)
    return qs.count()


def project_limit_reached(email: str | None) -> tuple[bool, str]:
    """Check if user has reached their project (organization) limit."""
    em = (email or "").strip().lower()
    if not em:
        return True, "Email is required."

    from apps.organizations.models import Organization

    count = Organization.objects.filter(owner_email=em).count()

    # Internal accounts bypass every project cap, individual or agency — see
    # effective_max_projects for why the individual cap does not apply to them.
    if is_internal_email(email):
        return False, ""

    # Individual accounts are single-brand by design (a brand IS a project).
    # This is a product invariant, so it is enforced even when the plan-limit
    # toggle is off — unlike the billing caps below.
    if not is_agency(email):
        if count >= 1:
            return True, (
                "Individual accounts include a single brand. Switch to an Agency "
                "account to manage multiple brands."
            )
        return False, ""

    # Agencies: honour the plan-limit enforcement toggle, then compare against
    # the effective (plan-derived, multi-brand) cap.
    if not is_plan_limits_enforcement_enabled():
        return False, ""

    max_projects = effective_max_projects(email)
    if count < max_projects:
        return False, ""

    # An agency with no active subscription has no plan to name — telling them
    # "your Self-Serve Brand plan allows 1 project" would be wrong on both
    # counts, since get_plan_limits() only defaulted them to starter.
    sub = _get_sub(email)
    if not (sub and sub.is_active):
        return True, (
            f"Agency accounts include {max_projects} brand(s) before checkout. "
            "Subscribe to add more client brands."
        )

    limits = get_plan_limits(email)
    return True, (
        f"Your {limits['label']} plan allows {max_projects} brand(s)."
        f"{_upgrade_hint_for_plan(_effective_plan_key(email))}"
    )


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
    count = _tracked_prompt_count(em)
    max_prompts = limits["max_prompts"]
    if count >= max_prompts:
        pk = _effective_plan_key(email)
        return True, (
            f"Your {limits['label']} plan allows {max_prompts} tracked prompts.{_upgrade_hint_for_plan(pk)}"
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
    count = _tracked_prompt_count(em)
    max_prompts = limits["max_prompts"]
    if count + additional > max_prompts:
        pk = _effective_plan_key(email)
        return True, (
            f"This run would exceed your {limits['label']} plan limit of {max_prompts} tracked prompts "
            f"(you have {count}, adding {additional})."
            f"{_upgrade_hint_for_plan(pk)}"
        )
    return False, ""


# ── Auto-fix / Analysis Count Quotas ──────────────────────────────────────
#
# The countable layer of cost control: a customer can reason about "30
# auto-fixes / 30 days" in a way they cannot about "$25 of LLM". The USD fuse
# (services.llm_spend.check_budget) stays the backstop for cost outliers.
#
# Quotas are scoped PER BRAND (organization), not per email: an agency's
# allowance is the sum of its brands' allowances, which maps onto the planned
# per-brand billing (see AGENCY_MAX_PROJECTS). Trailing windows, not calendar
# months, for the same anti-gaming reason as llm_spend.WINDOW_DAYS.
#
# Unlike the USD fuse these fail CLOSED past the cap: a row count cannot be
# ambiguously wrong the way a missed provider charge can.

QUOTA_WINDOW_DAYS = 30


def autofix_generations(since):
    """Auto-fix jobs that consumed an LLM generation, unscoped.

    Excludes rows that never call the LLM: verification audit rows,
    manual-walkthrough rows, and approve rows (marked via ``payload_sent`` —
    approving applies already-generated content, so it must not consume the
    generation quota a second time). Callers scope this to a brand
    (enforcement) or to an account's brands (the usage endpoint).
    """
    from apps.analyzer.models import AutoFixJob

    return (
        AutoFixJob.objects.filter(created_at__gte=since)
        .exclude(fix_type__in=("manual", "verification"))
        # Not ``payload_sent__contains`` (sqlite cannot compile it, so the quota
        # tests could never run) and not a bare ``.exclude(payload_sent__source=...)``:
        # ``payload_sent`` defaults to {}, so the key is SQL NULL on normal rows and
        # ``NOT (NULL = 'approve')`` is NULL, which would drop every generation row
        # instead of keeping it. Spelling the NULL case out keeps those rows on both
        # backends.
        .filter(Q(payload_sent__source__isnull=True) | ~Q(payload_sent__source="approve"))
    )


def _autofix_generation_qs(organization, since):
    """``autofix_generations`` scoped to one brand (the quota unit)."""
    return autofix_generations(since).filter(analysis_run__organization=organization)


def autofix_limit_reached(
    email: str | None, organization, additional: int = 1
) -> tuple[bool, str]:
    """Whether generating ``additional`` more auto-fixes would exceed the
    brand's daily or 30-day quota."""
    if is_internal_email(email):
        return False, ""
    if not is_plan_limits_enforcement_enabled():
        return False, ""
    if organization is None:
        return True, "No brand linked to this run."

    limits = get_plan_limits(email)
    pk = _effective_plan_key(email)

    monthly_cap = int(limits.get("max_autofixes_per_month", 0) or 0)
    if monthly_cap > 0:
        since = timezone.now() - timedelta(days=QUOTA_WINDOW_DAYS)
        used = _autofix_generation_qs(organization, since).count()
        if used + additional > monthly_cap:
            return True, (
                f"Your {limits['label']} plan includes {monthly_cap} auto-fixes per "
                f"{QUOTA_WINDOW_DAYS} days for this brand (you have used {used})."
                f"{_upgrade_hint_for_plan(pk)}"
            )

    daily_cap = int(limits.get("max_autofixes_per_day", 0) or 0)
    if daily_cap > 0:
        since = timezone.now() - timedelta(days=1)
        used = _autofix_generation_qs(organization, since).count()
        if used + additional > daily_cap:
            return True, (
                f"Your {limits['label']} plan allows {daily_cap} auto-fixes per day "
                f"for this brand. Try again tomorrow.{_upgrade_hint_for_plan(pk)}"
            )

    return False, ""


def autofix_regen_limit_reached(email: str | None, recommendation) -> tuple[bool, str]:
    """Whether this recommendation has exhausted its preview regenerations.

    The realistic abuse pattern is regenerating one preview in a loop, so the
    cap is per recommendation: 1 initial generation + ``max_autofix_regens``
    retries. Counts persisted preview rows (one row per actual generation).
    """
    if is_internal_email(email):
        return False, ""
    if not is_plan_limits_enforcement_enabled():
        return False, ""

    limits = get_plan_limits(email)
    max_regens = int(limits.get("max_autofix_regens", 0) or 0)
    if max_regens <= 0:
        return False, ""

    from apps.analyzer.models import AutoFixJob

    generations = (
        AutoFixJob.objects.filter(recommendation=recommendation, status=AutoFixJob.Status.PREVIEW)
        .exclude(fix_type="manual")  # manual walkthroughs never call the LLM
        .count()
    )
    if generations >= 1 + max_regens:
        return True, (
            f"This fix has already been generated {generations} times. Your "
            f"{limits['label']} plan allows {max_regens} regenerations per "
            f"recommendation.{_upgrade_hint_for_plan(_effective_plan_key(email))}"
        )
    return False, ""


def analysis_count_limit_reached(email: str | None, organization) -> tuple[bool, str]:
    """Whether starting one more analysis would exceed the brand's 30-day cap.

    Analyses are the expensive unit (measured $0.30-$3 each), so they get their
    own count separate from auto-fixes. Scoped to the organization when known;
    anonymous scans (no org) fall back to the email scope, and callers with
    neither are left to the throttles.
    """
    if is_internal_email(email):
        return False, ""
    if not is_plan_limits_enforcement_enabled():
        return False, ""

    limits = get_plan_limits(email)
    cap = int(limits.get("max_analyses_per_month", 0) or 0)
    if cap <= 0:
        return False, ""

    from apps.analyzer.models import AnalysisRun

    since = timezone.now() - timedelta(days=QUOTA_WINDOW_DAYS)
    qs = AnalysisRun.objects.filter(created_at__gte=since)
    if organization is not None:
        qs = qs.filter(organization=organization)
    else:
        em = (email or "").strip().lower()
        if not em:
            return False, ""
        qs = qs.filter(email__iexact=em)

    used = qs.count()
    if used >= cap:
        return True, (
            f"Your {limits['label']} plan includes {cap} analyses per "
            f"{QUOTA_WINDOW_DAYS} days for this brand (you have used {used})."
            f"{_upgrade_hint_for_plan(_effective_plan_key(email))}"
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
            f"The {eng} engine is not included on your {limits['label']} plan.{_upgrade_hint_for_plan(pk)}"
        )
    return True, ""
