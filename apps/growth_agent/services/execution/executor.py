"""
Prepares scheduled plan tasks, one at a time, with retries.

Lifecycle of a due task: claim (planned -> executing, row-locked so two workers
never prepare it twice) -> guardrails -> channel readiness -> prepare a draft
-> awaiting approval. A failed preparation is retried tomorrow, or fails after
MAX_ATTEMPTS. Every decision is written to the audit log.

Nothing is sent or published here. A prepared draft becomes an action that the
brand owner approves or rejects; sending an approved action is a separate step
(``services/control/dispatch.py``). That holds whatever a program's stored
channel mode says: an old "autonomous" setting no longer skips approval.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from ...models import AgentActionLog, ChannelMode, GrowthCycle, GrowthProgram, GrowthTask
from ..control import actions
from . import audit, guardrails
from .channel_base import ChannelOutcome
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
    """A worker that died while preparing leaves the task 'executing'; put it back.

    Only tasks with no action are released. A task that already has one is
    being sent, or its outcome is unknown, and preparing it again could lead to
    the same thing being sent twice.
    """
    cutoff = (now or timezone.now()) - STALE_EXECUTION
    return GrowthTask.objects.filter(
        status=GrowthTask.Status.EXECUTING, updated_at__lt=cutoff, actions__isnull=True
    ).update(status=GrowthTask.Status.PLANNED)


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
    try:
        prepared = channel.prepare(task)
    except Exception as exc:  # any crash in a channel must not kill the scheduler loop
        logger.exception("growth task %s crashed", task.id)
        _retry_or_fail(task, str(exc), {})
        return
    if isinstance(prepared, ChannelOutcome):  # preparation failed; nothing was sent
        _retry_or_fail(task, prepared.reason, prepared.payload, retryable=prepared.retryable)
        return
    if program.channel_modes.get(task.channel) == ChannelMode.DRY_RUN:
        # A dry-run channel only ever drafts: no action, so nothing to approve or send.
        _set(task, GrowthTask.Status.READY, result_payload=prepared.preview)
        audit.record(program, task, "publish", AgentActionLog.Outcome.DRY_RUN, prepared.preview)
        return
    actions.create_action(task, prepared)
    _set(task, GrowthTask.Status.AWAITING_APPROVAL)
