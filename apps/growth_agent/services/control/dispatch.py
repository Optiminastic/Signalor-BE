"""
The gate between an approved draft and the outside world.

Nothing is sent or published except through ``dispatch``. It runs in three
separate steps:

1. Claim, under a row lock: the action must be approved, the approval must be
   for exactly the stored content, and the content must still match its hash.
   Then the action is marked as being sent. Two workers can never both claim.
2. Execute, outside any transaction: the channel sends the stored draft.
3. Finalize: record what happened.

An outcome that is not known for certain is never treated as "not sent" and is
never retried here. It is parked as ``needs_reconciliation`` for a person.
"""

from __future__ import annotations

import logging
from datetime import date

from django.db import transaction
from django.db.models import QuerySet

from ...models import AgentActionLog, GrowthAction, GrowthCycle, GrowthProgram, GrowthTask
from ..execution import audit, guardrails
from ..execution.channel_base import ChannelOutcome
from ..execution.channels import REGISTRY
from . import actions, approvals

logger = logging.getLogger("apps")

AUDIT_PUBLISH = "publish"  # the action name the daily caps and the budget count
AUDIT_REFUSED = "dispatch_refused"
AUDIT_UNKNOWN = "outcome_unknown"
REASON_LIMIT = 500
NOT_APPROVED = "This draft has no valid approval."
TAMPERED = "The stored draft no longer matches what was approved."
CHANNEL_GONE = "This channel can no longer run."


def dispatchable(today: date) -> QuerySet[GrowthAction]:
    """Approved actions that are due, for brands that are running."""
    return (
        GrowthAction.objects.filter(
            state=GrowthAction.State.APPROVED,
            task__scheduled_for__lte=today,
            task__cycle__status=GrowthCycle.Status.ACTIVE,
            task__cycle__program__status=GrowthProgram.Status.ACTIVE,
            task__cycle__program__kill_switch=False,
        )
        .select_related("task")
        .order_by("task__scheduled_for", "created_at")
    )


def _refuse(action: GrowthAction, reason: str) -> None:
    """Close an action that must never be sent, and say why."""
    task = action.task
    action.state = GrowthAction.State.FAILED
    action.save(update_fields=["state", "updated_at"])
    task.status = GrowthTask.Status.FAILED
    task.result_payload = {**(task.result_payload or {}), "error": reason}
    task.save(update_fields=["status", "result_payload", "updated_at"])
    audit.record(
        task.cycle.program,
        task,
        AUDIT_REFUSED,
        AgentActionLog.Outcome.BLOCKED,
        {"action": action.public_id, "reason": reason},
    )


def _claim(action_id: int) -> GrowthAction | None:
    """Move one approved action to "being sent", or return None when it may not go."""
    with transaction.atomic():
        action = (
            GrowthAction.objects.select_for_update()
            .select_related("current_version", "task__cycle__program__organization")
            .filter(pk=action_id, state=GrowthAction.State.APPROVED)
            .first()
        )
        if action is None:
            return None
        task = action.task
        if task.channel not in REGISTRY:
            _refuse(action, CHANNEL_GONE)
            return None
        if not approvals.has_valid_approval(action):
            _refuse(action, NOT_APPROVED)
            return None
        if not actions.version_is_intact(action, action.current_version):
            _refuse(action, TAMPERED)
            return None
        decision = guardrails.check(task)
        if not decision.allowed:
            # Paused, kill switch on, or a daily cap reached: the approval
            # stands and the action simply waits for a later round.
            return None
        action.state = GrowthAction.State.DISPATCHING
        action.save(update_fields=["state", "updated_at"])
        task.status = GrowthTask.Status.EXECUTING
        task.save(update_fields=["status", "updated_at"])
        return action


def _finalize(action: GrowthAction, outcome: ChannelOutcome) -> None:
    task = action.task
    program = task.cycle.program
    reference = {"action": action.public_id}
    with transaction.atomic():
        if outcome.succeeded:
            action.state = GrowthAction.State.SUCCEEDED
            task.status = GrowthTask.Status.DONE
            task.evidence_url = outcome.evidence_url
            task.result_payload = outcome.payload
            detail = {**outcome.payload, "url": outcome.evidence_url, **reference}
            audit.record(program, task, AUDIT_PUBLISH, AgentActionLog.Outcome.SUCCEEDED, detail)
        elif not outcome.external_call_made:
            action.state = GrowthAction.State.FAILED
            task.status = GrowthTask.Status.FAILED
            task.result_payload = {**outcome.payload, "error": outcome.reason[:REASON_LIMIT]}
            detail = {**task.result_payload, **reference}
            audit.record(program, task, AUDIT_PUBLISH, AgentActionLog.Outcome.FAILED, detail)
        else:
            _park_unknown(action, outcome.reason, outcome.payload)
            return
        action.save(update_fields=["state", "updated_at"])
        task.save(update_fields=["status", "evidence_url", "result_payload", "updated_at"])


def _park_unknown(action: GrowthAction, reason: str, payload: dict | None = None) -> None:
    """The send may or may not have gone out. Stop, keep the task from being
    re-run, and leave the question for a person."""
    task = action.task
    with transaction.atomic():
        action.state = GrowthAction.State.NEEDS_RECONCILIATION
        action.save(update_fields=["state", "updated_at"])
        task.result_payload = {**(payload or {}), "outcome_unknown": reason[:REASON_LIMIT]}
        task.save(update_fields=["result_payload", "updated_at"])
        audit.record(
            task.cycle.program,
            task,
            AUDIT_UNKNOWN,
            AgentActionLog.Outcome.FAILED,
            {"action": action.public_id, "reason": reason[:REASON_LIMIT]},
        )


def dispatch(action_id: int) -> None:
    """Send one approved action, once. Safe to call twice: the second call finds
    nothing to claim."""
    action = _claim(action_id)
    if action is None:
        return
    try:
        outcome = REGISTRY[action.task.channel].execute(action.task, action.current_version)
    except Exception as exc:  # a crash mid-send leaves the outcome unknown, never "failed"
        logger.exception("growth action %s crashed while being sent", action.public_id)
        _park_unknown(action, str(exc))
        return
    _finalize(action, outcome)
