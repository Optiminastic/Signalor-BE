"""
The gate between an approved draft and the outside world.

Nothing is sent or published except through ``dispatch``. Each attempt runs in
four steps and leaves an append-only trail (``services/control/trail.py``):

1. Claim, under the action's row lock: the action must be approved, the
   approval valid (not expired) and for exactly the stored content, and the
   brand's guardrails must allow it. The action is marked as being sent and a
   ``claimed`` event is written. Two workers can never both claim.
2. Final check, under the lock again, immediately before the provider call:
   everything is checked once more, because the owner may have withdrawn the
   approval, or the kill switch may have been turned on, since the claim. If
   anything changed, the attempt ends ``aborted`` and nothing is sent.
   Otherwise ``provider_started`` is written and committed, and only then is
   the provider called.
3. The provider call, outside any transaction, using only the stored draft.
4. Result: ``provider_result`` is written with the outcome.

An outcome that is not known for certain is never treated as "not sent" and is
never retried here. It is parked as ``needs_reconciliation`` for a person. A
worker that dies part-way is handled by ``services/control/recovery.py``.
"""

from __future__ import annotations

import logging
from datetime import date

from django.db import OperationalError, transaction
from django.db.models import QuerySet

from ...models import AgentActionLog, GrowthAction, GrowthCycle, GrowthProgram, GrowthTask
from ..execution import audit, guardrails
from ..execution.channel_base import ChannelOutcome
from ..execution.channels import REGISTRY
from ..outreach.follow_up_actions import follow_up_block
from . import actions, approvals, expiry, ledger, trail

logger = logging.getLogger("apps")

Event = trail.Event
AUDIT_PUBLISH = "publish"  # the action name the daily caps and the budget count
AUDIT_REFUSED = "dispatch_refused"
AUDIT_UNKNOWN = "outcome_unknown"
REASON_LIMIT = 500
NOT_APPROVED = "This draft has no valid approval."
TAMPERED = "The stored draft no longer matches what was approved."
CHANNEL_GONE = "This channel can no longer run."
OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED = "failed"
OUTCOME_UNKNOWN = "unknown"
# Postgres deadlock and lock timeout: a lock conflict, not a bug in the action.
LOCK_CONFLICT_CODES = frozenset({"40P01", "55P03"})


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


def _locked(action_id: int, state: str) -> GrowthAction | None:
    return (
        actions.lock_actions()
        .select_related("current_version", "task__cycle__program__organization")
        .filter(pk=action_id, state=state)
        .first()
    )


def _refuse(action: GrowthAction, reason: str) -> None:
    """Close an action that must never be sent, and say why."""
    task = action.task
    action.state = GrowthAction.State.FAILED
    action.save(update_fields=["state", "updated_at"])
    if actions.updates_task(action):
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


def _blocking_reason(action: GrowthAction) -> str:
    """Why this action must never be sent as it stands, or "" if it may be.

    Expiry is handled before this (it has its own end state).
    """
    if action.task.channel not in REGISTRY:
        return CHANNEL_GONE
    if not approvals.has_valid_approval(action):
        return NOT_APPROVED
    if not actions.version_is_intact(action, action.current_version):
        return TAMPERED
    if action.kind == GrowthAction.Kind.SEND_FOLLOW_UP:
        return follow_up_block(action)
    return ""


def _set_task_status(action: GrowthAction, status: str) -> None:
    if not actions.updates_task(action):
        return
    action.task.status = status
    action.task.save(update_fields=["status", "updated_at"])


def _claim_step(action_id: int) -> tuple[GrowthAction | None, str]:
    """Step 1: move one approved action to "being sent". Returns the claimed
    action (carrying its attempt number), or None and how the attempt ended."""
    with transaction.atomic():
        action = _locked(action_id, GrowthAction.State.APPROVED)
        if action is None:
            return None, ledger.CLOSED
        if approvals.approval_expired(action):
            # Checked here, not only by the sweep, so an expired approval can
            # never be sent even if the sweep didn't run.
            expiry.expire_locked(action)
            return None, ledger.CLOSED
        reason = _blocking_reason(action)
        if reason:
            _refuse(action, reason)
            return None, ledger.CLOSED
        if not guardrails.check(action.task).allowed:
            # Paused, kill switch on, or a daily cap reached: the approval
            # stands and the action simply waits for a later round.
            return None, ledger.PREFLIGHT_WAIT
        action.dispatch_attempt = trail.next_attempt_number(action)
        trail.record(action, action.dispatch_attempt, Event.CLAIMED)
        action.state = GrowthAction.State.DISPATCHING
        action.save(update_fields=["state", "updated_at"])
        _set_task_status(action, GrowthTask.Status.EXECUTING)
        return action, ledger.PROVIDER_STARTED


def _claim(action_id: int) -> GrowthAction | None:
    """Step 1, returning only the claimed action (or None)."""
    return _claim_step(action_id)[0]


def _abort(action: GrowthAction, attempt: int, reason: str) -> None:
    trail.record(action, attempt, Event.ABORTED, reason=reason)


def _gate(claimed: GrowthAction) -> bool:
    """Step 2: the final check. True only if ``provider_started`` was committed
    and the call may proceed."""
    return _gate_step(claimed) == ledger.PROVIDER_STARTED


def _gate_step(claimed: GrowthAction) -> str:
    """Step 2: the final check, immediately before the provider call. Returns
    how it ended: ``PROVIDER_STARTED`` (committed; the call may proceed),
    ``PREFLIGHT_WAIT`` (a guardrail, approval kept) or ``CLOSED``."""
    attempt = claimed.dispatch_attempt
    with transaction.atomic():
        action = _locked(claimed.id, GrowthAction.State.DISPATCHING)
        if action is None:
            # Withdrawn (or otherwise moved on) since the claim. Nothing is sent.
            current = GrowthAction.objects.select_related("current_version").get(pk=claimed.id)
            _abort(current, attempt, f"The action became {current.state} before sending.")
            return ledger.CLOSED
        if approvals.approval_expired(action):
            _abort(action, attempt, "The approval expired before sending.")
            expiry.expire_locked(action)
            return ledger.CLOSED
        reason = _blocking_reason(action)
        if reason:
            _abort(action, attempt, reason)
            _refuse(action, reason)
            return ledger.CLOSED
        decision = guardrails.check(action.task)
        if not decision.allowed:
            _abort(action, attempt, decision.reason)
            action.state = GrowthAction.State.APPROVED
            action.save(update_fields=["state", "updated_at"])
            _set_task_status(action, GrowthTask.Status.AWAITING_APPROVAL)
            return ledger.PREFLIGHT_WAIT
        trail.record(action, attempt, Event.PROVIDER_STARTED)
        return ledger.PROVIDER_STARTED


def _finalize(action: GrowthAction, outcome: ChannelOutcome) -> None:
    """Step 4: record what the provider said."""
    task = action.task
    program = task.cycle.program
    reference = {"action": action.public_id}
    if outcome.external_call_made and not outcome.succeeded:
        park_unknown(action, action.dispatch_attempt, outcome.reason, outcome.payload)
        return
    with transaction.atomic():
        if outcome.succeeded:
            trail.record(action, action.dispatch_attempt, Event.PROVIDER_RESULT, outcome=OUTCOME_SUCCEEDED)
            action.state = GrowthAction.State.SUCCEEDED
            task.status = GrowthTask.Status.DONE
            task.evidence_url = outcome.evidence_url
            task.result_payload = outcome.payload
            detail = {**outcome.payload, "url": outcome.evidence_url, **reference}
            audit.record(program, task, AUDIT_PUBLISH, AgentActionLog.Outcome.SUCCEEDED, detail)
        else:
            trail.record(
                action,
                action.dispatch_attempt,
                Event.PROVIDER_RESULT,
                outcome=OUTCOME_FAILED,
                reason=outcome.reason,
            )
            action.state = GrowthAction.State.FAILED
            task.status = GrowthTask.Status.FAILED
            task.result_payload = {**outcome.payload, "error": outcome.reason[:REASON_LIMIT]}
            detail = {**task.result_payload, **reference}
            audit.record(program, task, AUDIT_PUBLISH, AgentActionLog.Outcome.FAILED, detail)
        action.save(update_fields=["state", "updated_at"])
        if actions.updates_task(action):
            task.save(update_fields=["status", "evidence_url", "result_payload", "updated_at"])


def park_unknown(action: GrowthAction, attempt: int, reason: str, payload: dict | None = None) -> None:
    """The send may or may not have gone out. Record that, stop, keep the task
    from being re-run, and leave the question for a person."""
    task = action.task
    with transaction.atomic():
        trail.record(action, attempt, Event.PROVIDER_RESULT, outcome=OUTCOME_UNKNOWN, reason=reason)
        action.state = GrowthAction.State.NEEDS_RECONCILIATION
        action.save(update_fields=["state", "updated_at"])
        if actions.updates_task(action):
            task.result_payload = {**(payload or {}), "outcome_unknown": reason[:REASON_LIMIT]}
            task.save(update_fields=["result_payload", "updated_at"])
        audit.record(
            task.cycle.program,
            task,
            AUDIT_UNKNOWN,
            AgentActionLog.Outcome.FAILED,
            {"action": action.public_id, "reason": reason[:REASON_LIMIT]},
        )


def _failure_kind(exc: Exception) -> str:
    code = getattr(exc.__cause__, "pgcode", None) or getattr(exc, "pgcode", None)
    return ledger.LOCK_CONFLICT if code in LOCK_CONFLICT_CODES else ledger.ERROR


def _prepare_send(action: GrowthAction, seq: int) -> GrowthAction | None:
    """Claim and final check, under a paid ticket. Always records the ticket's
    result. Returns the action only if the provider call may proceed."""
    outcome = ledger.ERROR
    try:
        claimed, outcome = _claim_step(action.id)
        if claimed is None:
            return None
        outcome = _gate_step(claimed)
        return claimed if outcome == ledger.PROVIDER_STARTED else None
    except OperationalError as exc:
        # The transaction rolled back: no provider_started, so nothing was sent.
        outcome = _failure_kind(exc)
        logger.warning("growth action %s: %s before sending", action.public_id, outcome)
        return None
    except Exception:  # noqa: BLE001 - counted as a failure; never sent
        logger.exception("growth action %s failed before sending", action.public_id)
        return None
    finally:
        ledger.record_result(action, seq, outcome)


def dispatch(action_id: int) -> None:
    """Send one approved action, once. Safe to call twice: the second call finds
    nothing to claim.

    Every attempt first reserves a ticket in the attempt ledger; with no ticket
    (not eligible yet, blocked, taken by another worker, or the ledger
    unavailable) nothing happens."""
    current = GrowthAction.objects.filter(pk=action_id, state=GrowthAction.State.APPROVED).first()
    if current is None:
        return
    seq = ledger.reserve(current)
    if seq is None:
        return
    action = _prepare_send(current, seq)
    if action is None:
        return
    try:
        outcome = REGISTRY[action.task.channel].execute(action.task, action.current_version)
    except Exception as exc:  # a crash mid-send leaves the outcome unknown, never "failed"
        logger.exception("growth action %s crashed while being sent", action.public_id)
        park_unknown(action, action.dispatch_attempt, str(exc))
        return
    _finalize(action, outcome)
