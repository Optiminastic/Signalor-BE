"""
Onboarding autopilot: moves a new program to an active month plan with no human step.

The program leaves onboarding in two steps, each of which already exists:
positioning report (built in the background from the latest completed
analysis), then the month plan (which flips the program to active). This
module chains them so nobody has to click through. It is safe to call often:
every step is idempotent, and a failed report is not retried on every visit.

Both steps need a completed site analysis. If the latest analysis failed (a
deploy orphaned it, a crash, a timeout), it is restarted here, at most
``MAX_RESTARTS_PER_HOUR`` times an hour and only within the owner's limits, so
a broken analysis can never loop. ``onboarding_stage`` says where setup is,
in plain words, for the chat agent and the owner.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from apps.analyzer.models import AnalysisRun

from ..analysis_tasks import dispatch_positioning_report
from ..models import GrowthProgram, PositioningReport
from . import onboarding
from .planning import plan as planning
from .positioning import report as positioning

logger = logging.getLogger("apps")
FAILED_REPORT_RETRY_AFTER = timedelta(hours=1)
MAX_RESTARTS_PER_HOUR = 2
RESTART_WINDOW = timedelta(hours=1)


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
    if report is None:
        _ensure_analysis(program)  # no completed analysis yet: make sure one is running
        return
    if needs_build:
        dispatch_positioning_report(report.id)


def _recent_failures(program: GrowthProgram) -> int:
    return AnalysisRun.objects.filter(
        organization=program.organization,
        status=AnalysisRun.Status.FAILED,
        created_at__gte=timezone.now() - RESTART_WINDOW,
    ).count()


def _ensure_analysis(program: GrowthProgram) -> None:
    latest = onboarding.latest_run(program.organization)
    if latest is not None and latest.status in onboarding.IN_FLIGHT_STATUSES:
        return
    if _recent_failures(program) >= MAX_RESTARTS_PER_HOUR:
        logger.warning("growth autopilot: analysis for program %s keeps failing; not restarting", program.pk)
        return
    if onboarding.restart_analysis(program.organization) is not None:
        logger.info("growth autopilot: started a site analysis for program %s", program.pk)


def _analysis_stage(program: GrowthProgram) -> str:
    latest = onboarding.latest_run(program.organization)
    if latest is not None and latest.status in onboarding.IN_FLIGHT_STATUSES:
        started = latest.created_at.strftime("%H:%M UTC")
        return (
            f"Signalor is analysing the brand's site (started {started}). The positioning report and the "
            "monthly plan are built automatically a few minutes after it finishes."
        )
    blocked = onboarding.analysis_block_reason(program.organization)
    if blocked:
        return f"Setup is waiting: a site analysis can't start. {blocked}"
    if latest is not None and latest.status == AnalysisRun.Status.FAILED:
        if _recent_failures(program) >= MAX_RESTARTS_PER_HOUR:
            return "The site analysis failed repeatedly in the last hour. It will be retried later."
        return "The last site analysis failed. A new one is starting now."
    return "Setup is starting: the first site analysis is about to begin."


def onboarding_stage(program: GrowthProgram) -> str:
    """Where the program is in setup, in plain words (never a guess)."""
    if program.status == GrowthProgram.Status.ACTIVE:
        return "The monthly plan is active."
    if program.status != GrowthProgram.Status.ONBOARDING:
        return f"The program is {program.status}."
    latest = positioning.latest_report(program)
    if latest is not None and latest.status == PositioningReport.Status.COMPLETE:
        return "The positioning report is done; the monthly plan is being built now."
    if latest is not None and latest.status == PositioningReport.Status.PENDING:
        return (
            "Signalor is building the positioning report (which prompts the brand is missing and which "
            "sites AI engines cite instead). The monthly plan follows automatically."
        )
    return _analysis_stage(program)
