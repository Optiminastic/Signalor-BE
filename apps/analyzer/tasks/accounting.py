"""LLM spend metering and the per-run budget fuse."""

import logging

from core.llm.client import get_collected_logs

from ..models import (
    AnalysisRun,
)

# Imported as modules, not names: a bare `from .accounting import x`
# binds at import time and makes `patch.object(accounting, 'x')` a no-op.
from . import progress  # noqa: F401

logger = logging.getLogger("apps")


def _budget_status(email: str):
    """Account's LLM budget status, or ``None`` if the check is unavailable."""
    try:
        from ..services.llm_spend import check_budget

        return check_budget(email)
    except Exception:
        logger.exception("Budget lookup failed for %s; allowing the run", email)
        return None


def _add_background_spend(run_id: int, spend: dict) -> None:
    """Fold work that finished after the run into its recorded cost.

    ``llm_cost_usd`` is what ``services.llm_spend`` sums for the budget window,
    so spend that never lands there is spend the fuse cannot see.
    """
    cost = float((spend or {}).get("cost", 0.0) or 0.0)
    if cost <= 0:
        return
    try:
        from django.db.models import F

        AnalysisRun.objects.filter(pk=run_id).update(llm_cost_usd=F("llm_cost_usd") + cost)
        logger.info(
            "Run %d: +$%.4f from %d background call(s) (competitive prompts)",
            run_id,
            cost,
            (spend or {}).get("calls", 0),
        )
    except Exception:
        logger.exception("Run %d: could not record background spend", run_id)


def _record_spend(run) -> None:
    """Persist this run's LLM spend so it can be metered and capped per account."""
    try:
        from ..services.llm_spend import record_run_cost

        record_run_cost(run)
    except Exception:
        logger.exception("Run %s: spend recording failed", getattr(run, "id", "?"))


def _record_run_spend(run, run_id: int) -> None:
    """Drain, persist and meter this run's LLM spend. Idempotent.

    Guarded on a non-empty drain because ``get_collected_logs()`` *clears* the
    collector as it reads. Two callers rely on that guard:

    * the partial-analysis path drains and records its own spend before
      returning, so an unconditional write here would overwrite its
      ``llm_logs`` with an empty list and reset ``llm_cost_usd`` to zero;
    * the success path calls this *before* dispatching competitive prompts, so
      the ``finally`` call that follows must be a no-op rather than a second,
      empty write.

    Writing ``llm_cost_usd`` absolutely (via ``record_run_cost``) is also why
    ordering matters against ``_add_background_spend``, which increments it with
    an ``F()`` expression. Recording first and incrementing later is safe; the
    reverse would silently clobber the background charge.
    """
    try:
        logs = get_collected_logs()
        if logs:
            run.llm_logs = logs
            run.save(update_fields=["llm_logs"])
            _record_spend(run)
            _log_run_cost(run_id, logs)
    except Exception:
        logger.exception("Run %s: cost accounting failed", run_id)


def _finalize_accounting(run, run_id: int) -> None:
    """Record spend and flush the trace. Runs from a ``finally``.

    Must cover every exit: a run that crashed halfway still made (and was billed
    for) real LLM calls, and skipping this used to lose both the spend and the
    buffered Langfuse events.

    ``_end_trace`` is deliberately *not* folded into ``_record_run_spend``. The
    success path records spend early, before dispatching competitive prompts,
    but the trace has to stay open across that dispatch: the daemon thread
    captures the run context when it is wrapped, so ending the trace first would
    strip user and trace attribution from those ~40 calls. It also runs outside
    the guarded block, so a broken meter cannot strand buffered events in a
    worker about to idle.
    """
    _record_run_spend(run, run_id)
    progress._end_trace(run_id)


def _log_run_cost(run_id: int, logs: list[dict]) -> None:
    """Emit one structured line with what this run cost and where it went.

    Every LLM call already records its exact OpenRouter charge; without this the
    numbers sat unread inside a 128 KB JSONField and the only way to find out
    what a run cost was the provider dashboard. Fail-soft: cost reporting must
    never break a completed run.
    """
    try:
        from core.llm.client import summarize_llm_logs

        summary = summarize_llm_logs(logs)
        top = list(summary["by_purpose"].items())[:5]
        logger.info(
            "Run %d LLM cost: $%.4f over %d calls (%d in / %d out tokens, %d cached, %d errors). "
            "Top spend: %s",
            run_id,
            summary["total_cost_usd"],
            summary["total_calls"],
            summary["total_tokens_in"],
            summary["total_tokens_out"],
            summary["cached_tokens"],
            summary["errors"],
            "; ".join(f"{p}=${b['cost']:.4f}({b['calls']})" for p, b in top),
        )
    except Exception:
        logger.exception("Run %d: LLM cost summary failed", run_id)

