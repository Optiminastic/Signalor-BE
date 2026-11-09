"""
Runs scheduled plan tasks autonomously, one at a time, with retries.

Lifecycle of a due task: claim (planned -> executing, row-locked so two workers
never run it twice) -> guardrails -> channel readiness -> run -> done, or retry
tomorrow, or failed after MAX_ATTEMPTS. Every decision is written to the audit log.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from ...models import AgentActionLog, ChannelMode, GrowthCycle, GrowthProgram, GrowthTask
from . import audit, guardrails
from .channels import REGISTRY

logger = logging.getLogger("apps")
MAX_ATTEMPTS = 3
STALE_EXECUTION = timedelta(hours=2)
REASON_LIMIT = 500


def due_tasks(today: date) -> QuerySet[GrowthTask]:
    return (
        GrowthTask.objects.filter(
            status=GrowthTask.Status.PLANNED,
            scheduled_for__lte=today,
            channel__in=list(REGISTRY),
            cycle__status=GrowthCycle.Status.ACTIVE,
            cycle__program__status=GrowthProgram.Status.ACTIVE,
            cycle__program__kill_switch=False,
        )
        .select_related("cycle__program__organization")
        .order_by("scheduled_for", "-impact_score")
    )


def release_stale_executions(now=None) -> int:
    """A worker that died mid-task leaves it 'executing'; put it back in the queue."""
    cutoff = (now or timezone.now()) - STALE_EXECUTION
    return GrowthTask.objects.filter(status=GrowthTask.Status.EXECUTING, updated_at__lt=cutoff).update(
        status=GrowthTask.Status.PLANNED
    )


def _claim(task_id: int) -> GrowthTask | None:
    with transaction.atomic():
        task = (
            GrowthTask.objects.select_for_update()
            .select_related("cycle__program__organization")
            .filter(pk=task_id, status=GrowthTask.Status.PLANNED)
            .first()
        )
        if task is None:
            return None
        task.status = GrowthTask.Status.EXECUTING
        task.attempts += 1
        task.save(update_fields=["status", "attempts", "updated_at"])
        return task


def _set(task: GrowthTask, status: str, **fields) -> None:
    task.status = status
    for name, value in fields.items():
        setattr(task, name, value)
    task.save()


def _wait(task: GrowthTask, reason: str) -> None:
    """Not runnable yet (channel not connected or daily cap): back to the queue, attempt not counted."""
    task.attempts = max(0, task.attempts - 1)
    _set(task, GrowthTask.Status.PLANNED, result_payload={**task.result_payload, "waiting_for": reason})


def _retry_or_fail(task: GrowthTask, reason: str, payload: dict, *, retryable: bool = True) -> None:
    detail = {**payload, "error": reason[:REASON_LIMIT], "attempt": task.attempts}
    if not retryable or task.attempts >= MAX_ATTEMPTS:
        _set(task, GrowthTask.Status.FAILED, result_payload=detail)
    else:
        tomorrow = timezone.now().date() + timedelta(days=1)
        _set(task, GrowthTask.Status.PLANNED, scheduled_for=tomorrow, result_payload=detail)
    audit.record(task.cycle.program, task, "publish", AgentActionLog.Outcome.FAILED, detail)


def run_task(task_id: int) -> None:
    task = _claim(task_id)
    if task is None:
        return
    program = task.cycle.program
    decision = guardrails.check(task)
    if not decision.allowed:
        if decision.retry_later:
            _wait(task, decision.reason)
        else:
            _set(task, GrowthTask.Status.SKIPPED, result_payload={"skipped": decision.reason})
            audit.record(
                program, task, "guardrail", AgentActionLog.Outcome.BLOCKED, {"reason": decision.reason}
            )
        return
    channel = REGISTRY[task.channel]
    not_ready = channel.readiness(program)
    if not_ready:
        _wait(task, not_ready)
        return
    mode = program.channel_modes.get(task.channel, ChannelMode.AUTONOMOUS)
    if mode == ChannelMode.APPROVAL:
        _set(task, GrowthTask.Status.AWAITING_APPROVAL)
        return
    try:
        outcome = channel.run(task, mode)
    except Exception as exc:  # any crash in a channel must not kill the scheduler loop
        logger.exception("growth task %s crashed", task.id)
        _retry_or_fail(task, str(exc), {})
        return
    if not outcome.succeeded:
        _retry_or_fail(task, outcome.reason, outcome.payload, retryable=outcome.retryable)
        return
    status = GrowthTask.Status.READY if outcome.dry_run else GrowthTask.Status.DONE
    _set(task, status, evidence_url=outcome.evidence_url, result_payload=outcome.payload)
    log_outcome = AgentActionLog.Outcome.DRY_RUN if outcome.dry_run else AgentActionLog.Outcome.SUCCEEDED
    audit.record(program, task, "publish", log_outcome, {**outcome.payload, "url": outcome.evidence_url})
