"""
Weekly visibility tracking for an active cycle.

Each snapshot re-fires the brand's tracked prompts across the AI engines
(Signalor's own ``recheck_track``), then measures only the fresh answers
against the cycle baseline. Re-checking costs real LLM calls, so requests are
guarded: one snapshot per scheduled week, manual checks at most once a day,
and an in-flight snapshot is always reused.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from django.conf import settings
from django.utils import timezone

from apps.analyzer.models import AnalysisRun, PromptTrack
from apps.analyzer.pipeline.prompt_tracker import recheck_track

from ...models import GrowthCycle, GrowthTask, WeeklySnapshot
from ..planning.schedule import DAYS_PER_WEEK, WEEKS_PER_CYCLE
from ..positioning.inputs import collect_inputs
from .measure import TaskRef, measure
from .reporter import send_weekly_email

logger = logging.getLogger("apps")
DEFAULT_RECHECK_WORKERS = 4
MANUAL_COOLDOWN = timedelta(hours=24)
ERROR_MESSAGE_LIMIT = 500


class TrackingError(Exception):
    """A snapshot can't be requested; the message is safe to show the user."""


def elapsed_week(cycle: GrowthCycle, today: date) -> int:
    """Completed cycle weeks as of ``today``, clamped to 0..WEEKS_PER_CYCLE."""
    weeks = (today - cycle.starts_on).days // DAYS_PER_WEEK
    return max(0, min(weeks, WEEKS_PER_CYCLE))


def _pending(cycle: GrowthCycle) -> WeeklySnapshot | None:
    return cycle.snapshots.filter(status=WeeklySnapshot.Status.PENDING).first()


def request_manual_snapshot(cycle: GrowthCycle) -> tuple[WeeklySnapshot, bool]:
    """Returns (snapshot, needs_run)."""
    pending = _pending(cycle)
    if pending:
        return pending, False
    recent = cycle.snapshots.filter(
        kind=WeeklySnapshot.Kind.MANUAL, started_at__gte=timezone.now() - MANUAL_COOLDOWN
    ).exists()
    if recent:
        raise TrackingError("Visibility was already re-checked in the last 24 hours.")
    week = max(1, elapsed_week(cycle, timezone.now().date()))
    return WeeklySnapshot.objects.create(cycle=cycle, week=week, kind=WeeklySnapshot.Kind.MANUAL), True


def request_scheduled_snapshot(cycle: GrowthCycle, today: date) -> WeeklySnapshot | None:
    """Creates the snapshot for the week that just ended, if it is due and not taken yet."""
    week = elapsed_week(cycle, today)
    if week < 1 or _pending(cycle):
        return None
    already = cycle.snapshots.filter(kind=WeeklySnapshot.Kind.SCHEDULED, week=week).exists()
    if already:
        return None
    return WeeklySnapshot.objects.create(cycle=cycle, week=week, kind=WeeklySnapshot.Kind.SCHEDULED)


def _baseline_run(cycle: GrowthCycle) -> AnalysisRun | None:
    report = cycle.positioning_report
    return report.analysis_run if report else None


def _recheck_prompts(run: AnalysisRun) -> None:
    tracks = list(
        PromptTrack.objects.filter(analysis_run=run, deleted_at__isnull=True).select_related("analysis_run")
    )
    brand_name = run.brand_name or (run.organization.name if run.organization_id else "")

    def recheck(track: PromptTrack) -> None:
        try:
            recheck_track(track, brand_name, run.url)
        except Exception:
            logger.exception("growth tracking: recheck failed for track %s", track.id)

    workers = getattr(settings, "GROWTH_RECHECK_WORKERS", DEFAULT_RECHECK_WORKERS)
    if workers <= 1:
        for track in tracks:
            recheck(track)
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(recheck, tracks))


def _previous_share(snapshot: WeeklySnapshot) -> float | None:
    previous = (
        snapshot.cycle.snapshots.filter(status=WeeklySnapshot.Status.COMPLETE)
        .exclude(pk=snapshot.pk)
        .order_by("-completed_at")
        .first()
    )
    return previous.share_of_voice if previous else None


def _done_tasks(cycle: GrowthCycle) -> list[TaskRef]:
    return [
        TaskRef(t.id, t.title, t.target, t.target_url)
        for t in cycle.tasks.filter(status=GrowthTask.Status.DONE)
    ]


def run_snapshot(snapshot_id: int) -> None:
    snapshot = WeeklySnapshot.objects.select_related("cycle__positioning_report__analysis_run").get(
        pk=snapshot_id
    )
    cycle = snapshot.cycle
    run = _baseline_run(cycle)
    if run is None:
        _fail(snapshot, "The baseline analysis for this plan no longer exists.")
        return
    try:
        _recheck_prompts(run)
        inputs = collect_inputs(run, since=snapshot.started_at)
        if not inputs.answers:
            _fail(snapshot, "No AI engine answered during the re-check. Try again later.")
            return
        result = measure(
            inputs,
            baseline_share_of_voice=cycle.baseline_share_of_voice,
            previous_share_of_voice=_previous_share(snapshot),
            baseline_gaps=cycle.positioning_report.citation_gaps if cycle.positioning_report else [],
            done_tasks=_done_tasks(cycle),
        )
    except Exception as exc:
        logger.exception("growth tracking snapshot %s failed", snapshot_id)
        _fail(snapshot, str(exc))
        return
    for field, value in result.items():
        setattr(snapshot, field, value)
    snapshot.status = WeeklySnapshot.Status.COMPLETE
    snapshot.completed_at = timezone.now()
    snapshot.save()
    if snapshot.kind == WeeklySnapshot.Kind.SCHEDULED:
        send_weekly_email(snapshot)
        _close_cycle_after_final_week(snapshot)


def _close_cycle_after_final_week(snapshot: WeeklySnapshot) -> None:
    if snapshot.week >= WEEKS_PER_CYCLE:
        GrowthCycle.objects.filter(pk=snapshot.cycle_id).update(status=GrowthCycle.Status.COMPLETED)


def _fail(snapshot: WeeklySnapshot, message: str) -> None:
    snapshot.status = WeeklySnapshot.Status.FAILED
    snapshot.error_message = message[:ERROR_MESSAGE_LIMIT]
    snapshot.completed_at = timezone.now()
    snapshot.save(update_fields=["status", "error_message", "completed_at"])
