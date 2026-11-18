"""
"Paste a link" onboarding for the Growth Agent.

Turns a pasted URL into a Signalor brand with a fresh analysis, reusing the
same rules as Signalor's own onboarding:

- An existing brand for this owner + domain is reused (never duplicated).
- A new brand counts against ``project_limit_reached`` (plan limit respected).
- A new analysis must pass ``analysis_allowed_for_email`` and the prompt cap.
- An analysis already running, or completed recently, is reused instead of
  burning another run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from urllib.parse import urlparse

from django.utils import timezone

from apps.accounts.subscription_utils import (
    analysis_allowed_for_email,
    project_limit_reached,
    prompt_batch_would_exceed,
)
from apps.analyzer.models import AnalysisRun
from apps.analyzer.run_guard import maybe_fail_stale
from apps.analyzer.tasks import start_analysis_task
from apps.organizations.models import Organization
from apps.organizations.utils import normalize_url

from .programs import owned_organizations

FRESH_ANALYSIS_WINDOW = timedelta(days=7)
PROMPTS_PER_ANALYSIS = 10
IN_FLIGHT_STATUSES = (
    AnalysisRun.Status.PENDING,
    AnalysisRun.Status.CRAWLING,
    AnalysisRun.Status.ANALYZING,
    AnalysisRun.Status.SCORING,
)


class Outcome(StrEnum):
    READY = "ready"  # brand has a usable analysis (running or recent)
    BLOCKED = "blocked"  # plan limit or subscription rule stopped it
    INVALID = "invalid"  # not a usable URL


@dataclass(frozen=True)
class OnboardResult:
    outcome: Outcome
    organization: Organization | None = None
    run: AnalysisRun | None = None
    created_brand: bool = False
    message: str = ""


def canonical_url(raw: str) -> str | None:
    """``acme.com/x`` -> ``https://acme.com``. Only http(s) hosts are accepted."""
    candidate = raw.strip()
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or "." not in parsed.hostname:
        return None
    return f"{parsed.scheme}://{parsed.hostname.lower()}"


def _brand_name(url: str) -> str:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    return host.split(".")[0].replace("-", " ").title() or host


def latest_run(org: Organization) -> AnalysisRun | None:
    """The brand's newest analysis. A run whose worker died is marked failed
    first (the analyzer's own stale check), so it never looks "still running"."""
    run = AnalysisRun.objects.filter(organization=org).order_by("-created_at").first()
    return maybe_fail_stale(run) if run is not None else None


def _reusable_run(org: Organization) -> AnalysisRun | None:
    run = latest_run(org)
    if run is None:
        return None
    if run.status in IN_FLIGHT_STATUSES:
        return run
    is_fresh = timezone.now() - run.created_at < FRESH_ANALYSIS_WINDOW
    return run if run.status == AnalysisRun.Status.COMPLETE and is_fresh else None


def _analysis_block_reason(email: str) -> str:
    allowed, reason = analysis_allowed_for_email(email)
    if not allowed:
        return reason
    exceeds, reason = prompt_batch_would_exceed(email, PROMPTS_PER_ANALYSIS)
    return reason if exceeds else ""


def _start_analysis(email: str, org: Organization) -> AnalysisRun:
    run = AnalysisRun.objects.create(
        organization=org,
        url=org.url,
        brand_name=org.name,
        email=email,
        run_type=AnalysisRun.RunType.SINGLE_PAGE,
        status=AnalysisRun.Status.PENDING,
    )
    start_analysis_task(run.id)
    return run


def analysis_block_reason(organization: Organization) -> str:
    """Why the owner can't start an analysis right now (plan or usage limits), or ""."""
    return _analysis_block_reason(organization.owner_email)


def restart_analysis(organization: Organization) -> AnalysisRun | None:
    """Start a new analysis for the brand unless one is running or the owner's
    limits forbid it. Returns the running analysis, or None if none could start."""
    run = latest_run(organization)
    if run is not None and run.status in IN_FLIGHT_STATUSES:
        return run
    if analysis_block_reason(organization):
        return None
    return _start_analysis(organization.owner_email, organization)


def _find_owned_brand(email: str, url: str) -> Organization | None:
    normalized = normalize_url(url)
    return owned_organizations(email).filter(normalized_url=normalized).order_by("created_at").first()


def onboard_from_url(email: str, raw_url: str) -> OnboardResult:
    url = canonical_url(raw_url)
    if url is None:
        return OnboardResult(Outcome.INVALID, message="That does not look like a website link.")

    org = _find_owned_brand(email, url)
    if org is None:
        return _onboard_new_brand(email, url)

    run = _reusable_run(org)
    if run is None:
        reason = _analysis_block_reason(email)
        if reason:
            return OnboardResult(Outcome.BLOCKED, organization=org, message=reason)
        run = _start_analysis(email, org)
    return OnboardResult(Outcome.READY, organization=org, run=run)


def _onboard_new_brand(email: str, url: str) -> OnboardResult:
    # Every check runs before the brand is created, so a blocked request
    # never leaves an orphan brand behind.
    reached, reason = project_limit_reached(email)
    if reached:
        return OnboardResult(Outcome.BLOCKED, message=reason)
    reason = _analysis_block_reason(email)
    if reason:
        return OnboardResult(Outcome.BLOCKED, message=reason)
    org = Organization.objects.create(name=_brand_name(url), url=url, owner_email=email)
    run = _start_analysis(email, org)
    return OnboardResult(Outcome.READY, organization=org, run=run, created_brand=True)
