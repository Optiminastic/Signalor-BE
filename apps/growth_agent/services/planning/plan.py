"""
Month plan lifecycle: create a cycle from the latest positioning report, read
it back, and let the user skip or restore tasks.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from ...models import GrowthCycle, GrowthProgram, GrowthTask, PositioningReport
from .candidates import build_candidates
from .schedule import DAYS_PER_WEEK, WEEKS_PER_CYCLE, schedule

USER_EDITABLE_STATUSES = {GrowthTask.Status.PLANNED, GrowthTask.Status.SKIPPED}
MAX_SHARE_OF_VOICE = 100.0


class PlanError(Exception):
    """A plan can't be made yet; the message is safe to show the user."""


def active_cycle(program: GrowthProgram) -> GrowthCycle | None:
    return program.cycles.filter(status=GrowthCycle.Status.ACTIVE).order_by("-created_at").first()


def _latest_complete_report(program: GrowthProgram) -> PositioningReport | None:
    return (
        program.positioning_reports.filter(status=PositioningReport.Status.COMPLETE)
        .order_by("-created_at")
        .first()
    )


def _report_payload(report: PositioningReport) -> dict:
    return {
        "headline": report.headline,
        "share_of_voice": report.share_of_voice,
        "citation_gaps": report.citation_gaps,
        "lost_prompts": report.lost_prompts,
        "entity_gaps": report.entity_gaps,
        "context_gaps": report.context_gaps,
    }


def _next_monday(today: date) -> date:
    return today + timedelta(days=(7 - today.weekday()) % 7)


def _cancel_untouched_cycle(cycle: GrowthCycle) -> None:
    started = cycle.tasks.exclude(status__in=USER_EDITABLE_STATUSES).exists()
    if started:
        raise PlanError("This plan already has tasks in progress, so it can't be rebuilt.")
    cycle.status = GrowthCycle.Status.CANCELLED
    cycle.save(update_fields=["status", "updated_at"])


def create_plan(program: GrowthProgram, *, rebuild: bool = False) -> tuple[GrowthCycle, bool]:
    """Returns (cycle, created). Idempotent: an active cycle is returned as-is,
    unless ``rebuild`` replaces a plan none of whose tasks has started yet."""
    existing = active_cycle(program)
    if existing and not rebuild:
        return existing, False
    report = _latest_complete_report(program)
    if report is None:
        raise PlanError("Build the positioning report first.")

    start = _next_monday(timezone.now().date())
    placed = schedule(build_candidates(_report_payload(report)), start)
    baseline = float((report.headline or {}).get("brand_share_of_voice", 0.0))
    with transaction.atomic():
        if existing:
            _cancel_untouched_cycle(existing)
        cycle = GrowthCycle.objects.create(
            program=program,
            positioning_report=report,
            starts_on=start,
            ends_on=start + timedelta(days=WEEKS_PER_CYCLE * DAYS_PER_WEEK - 1),
            baseline_share_of_voice=baseline,
            target_share_of_voice=min(MAX_SHARE_OF_VOICE, baseline + program.uplift_target_points),
        )
        GrowthTask.objects.bulk_create(
            GrowthTask(
                cycle=cycle,
                week=item.week,
                scheduled_for=item.scheduled_for,
                channel=item.spec.channel,
                tactic=item.spec.tactic,
                title=item.spec.title[:300],
                rationale=item.spec.rationale,
                target=item.spec.target[:500],
                target_url=item.spec.target_url,
                prompts=list(item.spec.prompts),
                impact_score=item.spec.score,
                effort=item.spec.effort,
            )
            for item in placed
        )
        if program.status == GrowthProgram.Status.ONBOARDING:
            program.status = GrowthProgram.Status.ACTIVE
            program.save(update_fields=["status", "updated_at"])
    return cycle, True


def set_task_status(cycle: GrowthCycle, task_id: int, new_status: str) -> GrowthTask | None:
    """Skip or restore a not-yet-started task. Returns None if not allowed or not found."""
    if new_status not in USER_EDITABLE_STATUSES:
        return None
    task = cycle.tasks.filter(pk=task_id, status__in=USER_EDITABLE_STATUSES).first()
    if task is None:
        return None
    task.status = new_status
    task.save(update_fields=["status", "updated_at"])
    return task
