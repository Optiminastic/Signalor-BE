"""
Positioning report lifecycle: request (idempotent per analysis) and build.
"""

from __future__ import annotations

import logging

from apps.analyzer.models import AnalysisRun

from ...models import GrowthProgram, PositioningReport
from .context import analyse_context
from .inputs import collect_inputs
from .metrics import compute_metrics

logger = logging.getLogger("apps")
ERROR_MESSAGE_LIMIT = 500


def latest_complete_run(program: GrowthProgram) -> AnalysisRun | None:
    return (
        AnalysisRun.objects.filter(organization=program.organization, status=AnalysisRun.Status.COMPLETE)
        .order_by("-created_at")
        .first()
    )


def latest_report(program: GrowthProgram) -> PositioningReport | None:
    return program.positioning_reports.order_by("-created_at").first()


def request_report(program: GrowthProgram) -> tuple[PositioningReport | None, bool]:
    """Returns (report, needs_build). Reuses a pending or complete report for the
    latest analysis so repeated clicks never pay for a second LLM call."""
    run = latest_complete_run(program)
    if run is None:
        return None, False
    existing = (
        program.positioning_reports.filter(analysis_run=run)
        .exclude(status=PositioningReport.Status.FAILED)
        .order_by("-created_at")
        .first()
    )
    if existing:
        return existing, False
    return PositioningReport.objects.create(program=program, analysis_run=run), True


def build_report(report_id: int) -> None:
    report = PositioningReport.objects.select_related("analysis_run", "program__organization").get(
        pk=report_id
    )
    if report.analysis_run is None:
        _fail(report, "The analysis this report was based on no longer exists.")
        return
    try:
        inputs = collect_inputs(report.analysis_run)
        metrics = compute_metrics(inputs)
        context = analyse_context(inputs, organization=report.program.organization)
    except Exception as exc:
        logger.exception("positioning report %s failed", report_id)
        _fail(report, str(exc))
        return
    for field, value in metrics.items():
        setattr(report, field, value)
    report.context_gaps = context
    report.status = PositioningReport.Status.COMPLETE
    report.save()


def _fail(report: PositioningReport, message: str) -> None:
    report.status = PositioningReport.Status.FAILED
    report.error_message = message[:ERROR_MESSAGE_LIMIT]
    report.save(update_fields=["status", "error_message", "updated_at"])
