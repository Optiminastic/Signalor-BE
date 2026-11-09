"""
Onboarding autopilot: moves a new program to an active month plan with no human step.

The program leaves onboarding in two steps, each of which already exists:
positioning report (built in the background from the latest completed
analysis), then the month plan (which flips the program to active). This
module chains them so nobody has to click through. It is safe to call often:
every step is idempotent, and a failed report is not retried on every visit.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from ..analysis_tasks import dispatch_positioning_report
from ..models import GrowthProgram, PositioningReport
from .planning import plan as planning
from .positioning import report as positioning

logger = logging.getLogger("apps")
FAILED_REPORT_RETRY_AFTER = timedelta(hours=1)


def _recently_failed(report: PositioningReport) -> bool:
    return (
        report.status == PositioningReport.Status.FAILED
        and timezone.now() - report.updated_at < FAILED_REPORT_RETRY_AFTER
    )


def _create_plan(program: GrowthProgram) -> None:
    try:
        planning.create_plan(program)
    except planning.PlanError:
        logger.info("growth autopilot: plan not ready for program %s", program.pk)


def advance_onboarding(program: GrowthProgram) -> None:
    """Takes the next onboarding step if one is possible; no-op once the program is active."""
    if program.status != GrowthProgram.Status.ONBOARDING:
        return
    latest = positioning.latest_report(program)
    if latest is not None and latest.status == PositioningReport.Status.COMPLETE:
        _create_plan(program)
        return
    if latest is not None and (latest.status == PositioningReport.Status.PENDING or _recently_failed(latest)):
        return
    report, needs_build = positioning.request_report(program)
    if report is not None and needs_build:
        dispatch_positioning_report(report.id)
