"""
Closing an action whose send outcome is unknown.

When a send was attempted but its result could not be confirmed, the action is
parked in ``needs_reconciliation`` and nothing retries it. Only the brand
owner can close it, in one of two ways:

- ``confirmed_sent``: they found the email or post, so it did go out.
- ``cancel``: close it with the outcome still unknown.

Neither sends anything. Asking for the action to be sent again is not offered
here: that would risk a duplicate, and is a separate decision.

Like approvals, a resolution names the exact version and hash the owner was
shown, runs under the action's row lock, and is written as an immutable
record plus an audit entry naming who made it.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from ...models import AgentActionLog, ApprovalDecision, GrowthAction, GrowthProgram, GrowthTask
from ..execution import audit
from . import actions
from .approvals import ActionNotFound, DecisionRefused, matches_current

ROLE_OWNER = "owner"
REASON_MAX_CHARS = 500
CONFIRMED_SENT = "confirmed_sent"
CANCEL = "cancel"
RESOLUTIONS = (CONFIRMED_SENT, CANCEL)

# The daily caps and monthly budget count "publish" entries. A send the owner
# confirmed did go out, so it is counted like any other.
AUDIT_PUBLISH = "publish"
AUDIT_CLOSED = "outcome_closed"
CLOSED_NOTE = "Closed by the brand owner with the outcome unknown."
CONFIRMED_NOTE = "Confirmed sent by the brand owner."


@dataclass(frozen=True)
class Outcome:
    decision: str
    state: str
    task_status: str


OUTCOMES = {
    CONFIRMED_SENT: Outcome(
        ApprovalDecision.Decision.RESOLVED_SENT, GrowthAction.State.SUCCEEDED, GrowthTask.Status.DONE
    ),
    CANCEL: Outcome(
        ApprovalDecision.Decision.RESOLVED_CANCELLED, GrowthAction.State.CANCELLED, GrowthTask.Status.SKIPPED
    ),
}


@dataclass(frozen=True)
class ResolutionRequest:
    resolution: str
    version_number: int
    payload_hash: str
    actor_email: str
    reason: str = ""


def _locked_action(program: GrowthProgram, public_id: str) -> GrowthAction:
    action = (
        actions.lock_actions()
        .select_related("current_version", "task")
        .filter(organization=program.organization, public_id=public_id)
        .first()
    )
    if action is None:
        raise ActionNotFound(public_id)
    return action


def _settle_task(task: GrowthTask, resolution: str) -> None:
    outcome = OUTCOMES[resolution]
    note_key, note = (
        ("resolved", CONFIRMED_NOTE) if resolution == CONFIRMED_SENT else ("skipped", CLOSED_NOTE)
    )
    task.status = outcome.task_status
    task.result_payload = {**(task.result_payload or {}), note_key: note}
    task.save(update_fields=["status", "result_payload", "updated_at"])


def _audit(program: GrowthProgram, action: GrowthAction, request: ResolutionRequest) -> None:
    confirmed = request.resolution == CONFIRMED_SENT
    audit.record(
        program,
        action.task,
        AUDIT_PUBLISH if confirmed else AUDIT_CLOSED,
        AgentActionLog.Outcome.SUCCEEDED,
        {"action": action.public_id, "version": request.version_number, "resolution": request.resolution},
        actor_email=request.actor_email,
    )


def resolve(program: GrowthProgram, public_id: str, request: ResolutionRequest) -> GrowthAction:
    """Record the owner's resolution, or raise ``DecisionRefused``/``ActionNotFound``."""
    if request.resolution not in RESOLUTIONS:
        raise DecisionRefused("invalid_resolution", "That is not a way to close this action.")
    outcome = OUTCOMES[request.resolution]
    with transaction.atomic():
        action = _locked_action(program, public_id)
        if action.state == outcome.state and matches_current(action, request):
            return action  # a repeat of the resolution already in force
        if action.state != GrowthAction.State.NEEDS_RECONCILIATION:
            raise DecisionRefused("illegal_state", f"This action is {action.state} and can no longer change.")
        if not matches_current(action, request):
            raise DecisionRefused("stale", "This action changed since you reviewed it. Review it again.")

        ApprovalDecision.objects.create(
            organization=action.organization,
            action=action,
            version=action.current_version,
            payload_hash=action.current_version.payload_hash,
            decision=outcome.decision,
            actor_email=request.actor_email,
            actor_role=ROLE_OWNER,
            reason=request.reason[:REASON_MAX_CHARS],
        )
        action.state = outcome.state
        action.save(update_fields=["state", "updated_at"])
        _settle_task(action.task, request.resolution)
        _audit(program, action, request)
    return action
