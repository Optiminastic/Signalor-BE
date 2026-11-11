"""
The brand owner's decision on a draft action.

A decision is always about one exact version: the caller states the version
number and the content hash they reviewed, and the decision is refused if
either no longer matches. Every decision is written as an immutable record and
as an audit entry naming who made it.

Approving does not send anything. Sending is a separate step (``dispatch``)
that re-checks the approval before it acts.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from ...models import AgentActionLog, ApprovalDecision, GrowthAction, GrowthProgram, GrowthTask
from ..execution import audit

ROLE_OWNER = "owner"
REJECTED_NOTE = "Rejected by the brand owner."
REASON_MAX_CHARS = 500

AUDIT_ACTION = {
    ApprovalDecision.Decision.APPROVED: "approved",
    ApprovalDecision.Decision.REJECTED: "rejected",
}
# Which states a decision may be taken from. A rejection is also allowed after
# an approval, as long as the action has not started being sent.
ALLOWED_FROM = {
    ApprovalDecision.Decision.APPROVED: (GrowthAction.State.AWAITING_APPROVAL,),
    ApprovalDecision.Decision.REJECTED: (
        GrowthAction.State.AWAITING_APPROVAL,
        GrowthAction.State.APPROVED,
    ),
}
RESULTING_STATE = {
    ApprovalDecision.Decision.APPROVED: GrowthAction.State.APPROVED,
    ApprovalDecision.Decision.REJECTED: GrowthAction.State.REJECTED,
}


class ActionNotFound(Exception):
    """No such action for this brand. Also raised for another brand's action."""


class DecisionRefused(Exception):
    """The decision cannot be recorded. ``code`` says why, for the API."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DecisionRequest:
    decision: str
    version_number: int
    payload_hash: str
    actor_email: str
    reason: str = ""


def _locked_action(program: GrowthProgram, public_id: str) -> GrowthAction:
    action = (
        GrowthAction.objects.select_for_update()
        .select_related("current_version", "task")
        .filter(organization=program.organization, public_id=public_id)
        .first()
    )
    if action is None:
        raise ActionNotFound(public_id)
    return action


def _already_decided(action: GrowthAction, request: DecisionRequest) -> bool:
    """A repeat of the decision already in force is a no-op, not an error, so a
    retried or replayed request can never create a second record."""
    return action.state == RESULTING_STATE[request.decision] and _matches_current(action, request)


def _matches_current(action: GrowthAction, request: DecisionRequest) -> bool:
    version = action.current_version
    return (
        version is not None
        and version.number == request.version_number
        and version.payload_hash == request.payload_hash
    )


def decide(program: GrowthProgram, public_id: str, request: DecisionRequest) -> GrowthAction:
    """Record ``request`` on the action, or raise. Runs under a row lock, so two
    decisions on the same action are always applied one after the other."""
    with transaction.atomic():
        action = _locked_action(program, public_id)
        if _already_decided(action, request):
            return action
        if action.state not in ALLOWED_FROM[request.decision]:
            raise DecisionRefused("illegal_state", f"This action is {action.state} and can no longer change.")
        if not _matches_current(action, request):
            raise DecisionRefused("stale", "The draft changed since you reviewed it. Review it again.")

        ApprovalDecision.objects.create(
            organization=action.organization,
            action=action,
            version=action.current_version,
            payload_hash=action.current_version.payload_hash,
            decision=request.decision,
            actor_email=request.actor_email,
            actor_role=ROLE_OWNER,
            reason=request.reason[:REASON_MAX_CHARS],
        )
        action.state = RESULTING_STATE[request.decision]
        action.save(update_fields=["state", "updated_at"])
        if request.decision == ApprovalDecision.Decision.REJECTED:
            _skip_task(action.task)
        audit.record(
            program,
            action.task,
            AUDIT_ACTION[request.decision],
            AgentActionLog.Outcome.SUCCEEDED,
            {"action": action.public_id, "version": request.version_number},
            actor_email=request.actor_email,
        )
    return action


def _skip_task(task: GrowthTask) -> None:
    task.status = GrowthTask.Status.SKIPPED
    task.result_payload = {**(task.result_payload or {}), "skipped": REJECTED_NOTE}
    task.save(update_fields=["status", "result_payload", "updated_at"])


def has_valid_approval(action: GrowthAction) -> bool:
    """Whether the newest decision on the current version is an approval, made
    for exactly this content."""
    version = action.current_version
    if version is None:
        return False
    latest = action.decisions.filter(version=version).order_by("-created_at", "-id").first()
    return (
        latest is not None
        and latest.decision == ApprovalDecision.Decision.APPROVED
        and latest.payload_hash == version.payload_hash
    )
