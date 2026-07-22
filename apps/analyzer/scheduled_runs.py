"""Execute one due ScheduledAnalysis: re-scan the site, refresh tasks, send the digest.

Extracted from the ``run_scheduled_analyses`` command so the same body runs on the
analysis worker. The command is now only a dispatcher; this is the work.

Two properties this module exists to guarantee:

**No drift.** The next run is anchored to the *scheduled* time, never to "now".
Anchoring to now compounds the cron's up-to-30-minute latency plus the run's own
duration into every cycle, walking a weekly scan across the clock (~26h/year).
This mirrors ``apps.drip.scheduling``, whose offsets are anchored to
``entered_at`` for the same reason.

**At most one run per due window.** RabbitMQ is at-least-once, and the cron can
overlap itself, so the same schedule can be dispatched twice. The claim below is
a compare-and-swap on ``next_run_at``: the row's own due time is the version
token, so exactly one caller can move it. No new column, no lock table.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.utils import timezone

from .models import AnalysisRun, ScheduledAnalysis

logger = logging.getLogger("apps")

FREQUENCY_INTERVAL = {
    ScheduledAnalysis.Frequency.WEEKLY: timedelta(days=7),
    ScheduledAnalysis.Frequency.MONTHLY: timedelta(days=30),
}


def next_run_after(previous_next_run_at: datetime, frequency: str, now: datetime) -> datetime:
    """The next due time, anchored to the previous one so the schedule cannot drift.

    Catches up past a gap (an outage, a paused cron) by advancing whole intervals
    until the result is in the future — so three missed weeks produce ONE run at
    the next slot, not a burst of three.
    """
    delta = FREQUENCY_INTERVAL.get(frequency, timedelta(days=7))
    nxt = previous_next_run_at + delta
    while nxt <= now:
        nxt += delta
    return nxt


def claim_due_schedule(schedule_id: int, *, now: datetime | None = None) -> ScheduledAnalysis | None:
    """Atomically take ownership of a due schedule, or return None.

    Reschedules *before* the analysis runs, deliberately: a worker that dies
    mid-analysis must not leave ``next_run_at`` in the past, or the next cron tick
    re-fires the same brand every 30 minutes. Losing one weekly scan is cheap;
    an infinite re-fire loop spends real LLM credits.
    """
    now = now or timezone.now()

    schedule = ScheduledAnalysis.objects.filter(pk=schedule_id, is_active=True, next_run_at__lte=now).first()
    if schedule is None:
        return None

    if schedule.frequency == ScheduledAnalysis.Frequency.ONCE:
        updates = {"is_active": False, "last_run_at": now}
    else:
        updates = {
            "next_run_at": next_run_after(schedule.next_run_at, schedule.frequency, now),
            "last_run_at": now,
        }

    # CAS: only the caller that still sees the old (next_run_at, is_active) wins.
    # `is_active=True` is essential for ONCE schedules — their update flips
    # is_active→False but does NOT change next_run_at, so a next_run_at-only
    # predicate would let two overlapping workers both claim and double-run.
    won = ScheduledAnalysis.objects.filter(
        pk=schedule_id, next_run_at=schedule.next_run_at, is_active=True
    ).update(**updates)
    if not won:
        logger.info("schedule %s already claimed by another worker; skipping", schedule_id)
        return None

    schedule.refresh_from_db()
    return schedule


def _previous_score(schedule: ScheduledAnalysis) -> float | None:
    prev = (
        AnalysisRun.objects.filter(organization=schedule.organization, status="complete")
        .order_by("-created_at")
        .first()
    )
    return prev.composite_score if prev else None


def _sync_tasks_and_notify(schedule: ScheduledAnalysis, run: AnalysisRun, prev_score) -> None:
    """Materialize the run's recommendations into tasks, then email the digest."""
    from apps.analyzer.email_utils import send_digest_email

    from .action_sync import materialize_run_actions

    try:
        created, _total = materialize_run_actions(run, schedule.organization.owner_email)
        if created:
            logger.info("scheduled run %s synced %d task(s)", run.id, created)
    except Exception:
        logger.exception("scheduled run %s: task sync failed", run.id)

    score_change = None
    if prev_score is not None and run.composite_score is not None:
        score_change = round(run.composite_score - prev_score, 1)

    top_recs = list(run.recommendations.order_by("priority")[:3].values("title", "priority", "category"))

    try:
        send_digest_email(
            to_email=schedule.email,
            context={
                "brand_name": schedule.brand_name or schedule.url,
                "url": schedule.url,
                "score": round(run.composite_score or 0),
                "score_change": score_change,
                "prev_score": round(prev_score) if prev_score else None,
                "recommendations": top_recs,
                "slug": run.slug,
            },
        )
    except Exception:
        logger.exception("scheduled run %s: digest email failed", run.id)


def execute_scheduled_analysis(schedule_id: int) -> bool:
    """Run one due schedule end to end. Returns True if it ran.

    Safe to call twice for the same id: the second call loses the claim and
    returns False.
    """
    from .tasks import _kickoff_sitemap_audit, run_single_page_analysis

    schedule = claim_due_schedule(schedule_id)
    if schedule is None:
        return False

    # One analysis at a time per brand: if a manual run is already in flight for
    # this org, skip the weekly one. The claim already advanced next_run_at, so
    # this brand simply gets its scan next cycle rather than a concurrent run.
    from .run_guard import active_run_for

    active = active_run_for(schedule.organization)
    if active is not None:
        logger.info(
            "schedule %s: skipping — run %s already in flight for org %s",
            schedule_id,
            active.id,
            schedule.organization_id,
        )
        return False

    prev_score = _previous_score(schedule)

    run = AnalysisRun.objects.create(
        organization=schedule.organization,
        url=schedule.url,
        email=schedule.email,
        brand_name=schedule.brand_name,
        run_type="single_page",
        status="pending",
    )
    ScheduledAnalysis.objects.filter(pk=schedule.pk).update(last_run_slug=run.slug)

    _kickoff_sitemap_audit(run.id)
    run_single_page_analysis(run.id)
    run.refresh_from_db()

    if run.status == "complete":
        _sync_tasks_and_notify(schedule, run, prev_score)
    else:
        logger.warning("scheduled run %s finished with status=%s", run.id, run.status)

    return True
