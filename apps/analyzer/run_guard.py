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

from datetime import timedelta

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
