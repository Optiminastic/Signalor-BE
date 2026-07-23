"""One analysis at a time per brand.

A single brand should never have two analyses running at once — concurrent runs
double the LLM / DataForSEO spend, race on the same recommendations, and confuse
the dashboard. The existing views only deduped by ``(org, url)``, so the same
brand could still start parallel runs on different URLs, and the weekly cron
could overlap a manual run.

This centralizes the check. Every org-scoped start point asks
``active_run_for(org)`` before creating a run.

Anonymous / free-tool runs have no organization, so the rule doesn't apply to
them — they are one-off and self-limited by plan gates elsewhere.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from .models import AnalysisRun

IN_FLIGHT_STATUSES = (
    AnalysisRun.Status.PENDING,
    AnalysisRun.Status.CRAWLING,
    AnalysisRun.Status.ANALYZING,
    AnalysisRun.Status.SCORING,
)

# A run that has been "in flight" longer than this is treated as dead, so a crash
# that never marked the row FAILED can't block the brand forever. The analysis
# worker's own hard time limit is 40 min (config/celery_rabbit.py), so anything
# past 45 min is definitively not still running.
STALE_AFTER = timedelta(minutes=45)

# Self-heal timeouts for orphaned runs whose background worker died. A run can be
# stuck in two very different "not done" states:
#   • PENDING  → still queued waiting for a worker; normal, give it a long grace.
#   • running (crawling/analyzing/scoring) → a worker picked it up and refreshes
#     updated_at as it progresses, so 5 min of silence means that worker is gone.
STALE_PENDING_TIMEOUT = timedelta(minutes=30)
STALE_RUNNING_TIMEOUT = timedelta(minutes=5)


def maybe_fail_stale(run: AnalysisRun) -> AnalysisRun:
    """Mark a silently-orphaned run FAILED so pollers stop waiting forever.

    A background worker refreshes ``updated_at`` as it advances; once a non-terminal
    run has been silent past its timeout, the worker has died (redeploy, crash,
    instance recycle) and the run will never finish on its own. Terminal and
    still-fresh runs are returned unchanged. Idempotent — safe to call on every poll.
    """
    if run.status in (AnalysisRun.Status.COMPLETE, AnalysisRun.Status.FAILED):
        return run
    is_pending = run.status == AnalysisRun.Status.PENDING
    timeout = STALE_PENDING_TIMEOUT if is_pending else STALE_RUNNING_TIMEOUT
    if run.updated_at >= timezone.now() - timeout:
        return run
    run.status = AnalysisRun.Status.FAILED
    if not run.error_message:
        mins = int(timeout.total_seconds() // 60)
        run.error_message = (
            f"Analysis stalled — no progress for over {mins} minutes, so the "
            "background worker likely restarted. Please re-run the analysis."
        )
    run.save(update_fields=["status", "error_message", "updated_at"])
    return run


# A completed analysis holds for 24h before another may start. The daily cadence
# already reflects real GEO movement, and re-running sooner just burns LLM /
# DataForSEO spend on data that has not changed. Keyed on the last COMPLETE run so
# a failed or in-flight run never starts the clock — a failure stays retriable.
ANALYSIS_COOLDOWN = timedelta(hours=24)


def cooldown_until(organization) -> datetime | None:
    """When the brand may next start an analysis, or None if it may start now.

    A brand gets one completed analysis per 24h. Failed / in-flight runs don't
    start the clock (a failure should be immediately retriable), and anonymous
    free-tool scans have no organization, so they are never gated here.
    """
    if organization is None:
        return None
    last_complete = (
        AnalysisRun.objects.filter(
            organization=organization,
            status=AnalysisRun.Status.COMPLETE,
        )
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    if last_complete is None:
        return None
    ready_at = last_complete + ANALYSIS_COOLDOWN
    return ready_at if ready_at > timezone.now() else None


def active_run_for(organization) -> AnalysisRun | None:
    """The brand's currently-running analysis, or None.

    Ignores runs older than ``STALE_AFTER`` so a stuck row doesn't wedge the brand.
    """
    if organization is None:
        return None
    cutoff = timezone.now() - STALE_AFTER
    return (
        AnalysisRun.objects.filter(
            organization=organization,
            status__in=IN_FLIGHT_STATUSES,
            created_at__gte=cutoff,
        )
        .order_by("-created_at")
        .first()
    )
