"""Run status, sub-progress and tracing."""

import logging

from django.utils import timezone

from ..models import (
    AnalysisRun,
)

logger = logging.getLogger("apps")


def _start_trace(run: AnalysisRun) -> None:
    """Group this run's LLM calls under one Langfuse trace. Never raises."""
    try:
        from core.observability.tracing import start_run

        org = getattr(run, "organization", None)
        email = (run.email or "").strip().lower()
        start_run(
            run.id,
            url=run.url or "",
            organization=getattr(org, "name", "") or "",
            # user_id drives Langfuse's per-user cost view, which is the same
            # question the budget fuse answers. session_id groups an org's runs.
            user_id=email,
            session_id=f"org-{org.id}" if org else (email or f"run-{run.id}"),
            tags=[t for t in ("analysis", run.country or "") if t],
            extra={"country": run.country or ""},
        )
    except Exception:
        logger.warning("Run %s: Langfuse trace start failed", getattr(run, "id", "?"), exc_info=True)


def _end_trace(run_id: int) -> None:
    """Flush buffered traces so a redeployed worker cannot lose them."""
    try:
        from core.observability.tracing import end_run

        end_run()
    except Exception:
        logger.warning("Run %s: Langfuse flush failed", run_id, exc_info=True)


def _update_status(run: AnalysisRun, status: str, progress: int = 0, phase: str = ""):
    run.status = status
    run.progress = progress
    if phase:
        run.phase = phase[:140]
    run.save(update_fields=["status", "progress", "phase", "updated_at"])


def _update_sub_progress(run: AnalysisRun, start: int, end: int, done: int, total: int, phase: str) -> None:
    """Advance the bar *within* a long phase, so it moves while work happens.

    The two slowest stages - firing prompts across every answer engine, and
    discovering competitors - each sat on a single checkpoint for the majority of
    the run. Both already iterate ``as_completed``, so they know how many items
    are finished; this turns that into movement instead of a frozen number.

    Fail-soft and never raises: a progress write must not be able to kill a run
    that is otherwise succeeding.
    """
    if total <= 0:
        return
    try:
        pct = start + int((end - start) * (min(done, total) / total))
        AnalysisRun.objects.filter(pk=run.pk).update(
            progress=pct, phase=f"{phase} ({done}/{total})"[:140], updated_at=timezone.now()
        )
    except Exception:
        logger.debug("Sub-progress update failed for run %s", getattr(run, "pk", "?"), exc_info=True)

