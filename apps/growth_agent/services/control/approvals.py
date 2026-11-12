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
from datetime import datetime, timedelta
from typing import Protocol

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ...models import AgentActionLog, ApprovalDecision, GrowthAction, GrowthProgram, GrowthTask
from ..execution import audit
from . import actions, trail

ROLE_OWNER = "owner"
REJECTED_NOTE = "Rejected by the brand owner."
REVOKED_NOTE = "Approval withdrawn by the brand owner."
# How long an approval counts. After this the action expires and is never sent.
APPROVAL_TTL = timedelta(days=getattr(settings, "GROWTH_APPROVAL_TTL_DAYS", 14))
REASON_MAX_CHARS = 500
IN_FLIGHT = "in_flight"
IN_FLIGHT_MESSAGE = "This is being sent right now and can't be stopped. Its result will show here shortly."

AUDIT_ACTION = {
    ApprovalDecision.Decision.APPROVED: "approved",
    ApprovalDecision.Decision.REJECTED: "rejected",
    ApprovalDecision.Decision.REVOKED: "revoked",
}
# Which states a decision may be taken from. A rejection is also allowed after
# an approval, as long as the action has not started being sent. Revoking
# withdraws an approval, before sending starts.
ALLOWED_FROM = {
    ApprovalDecision.Decision.APPROVED: (GrowthAction.State.AWAITING_APPROVAL,),
    ApprovalDecision.Decision.REJECTED: (
        GrowthAction.State.AWAITING_APPROVAL,
        GrowthAction.State.APPROVED,
    ),
    # Also while claimed for sending, until the provider call starts.
    ApprovalDecision.Decision.REVOKED: (GrowthAction.State.APPROVED, GrowthAction.State.DISPATCHING),
}
RESULTING_STATE = {
    ApprovalDecision.Decision.APPROVED: GrowthAction.State.APPROVED,
    ApprovalDecision.Decision.REJECTED: GrowthAction.State.REJECTED,
    ApprovalDecision.Decision.REVOKED: GrowthAction.State.REVOKED,
}
SKIPPED_NOTE = {
    ApprovalDecision.Decision.REJECTED: REJECTED_NOTE,
    ApprovalDecision.Decision.REVOKED: REVOKED_NOTE,
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
        actions.lock_actions()
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
    return action.state == RESULTING_STATE[request.decision] and matches_current(action, request)


class ReviewedDraft(Protocol):
    """Anything that names the exact draft a person reviewed."""

    version_number: int
    payload_hash: str


def matches_current(action: GrowthAction, request: ReviewedDraft) -> bool:
    """Whether the request names the action's current version and its hash."""
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
        if action.state == GrowthAction.State.DISPATCHING and trail.provider_call_in_progress(action):
            raise DecisionRefused(IN_FLIGHT, IN_FLIGHT_MESSAGE)
        if action.state not in ALLOWED_FROM[request.decision]:
            raise DecisionRefused("illegal_state", f"This action is {action.state} and can no longer change.")
        if not matches_current(action, request):
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
        if request.decision in SKIPPED_NOTE:
            skip_task(action.task, SKIPPED_NOTE[request.decision])
        audit.record(
            program,
            action.task,
            AUDIT_ACTION[request.decision],
            AgentActionLog.Outcome.SUCCEEDED,
            {"action": action.public_id, "version": request.version_number},
            actor_email=request.actor_email,
        )
    return action


def skip_task(task: GrowthTask, note: str) -> None:
    task.status = GrowthTask.Status.SKIPPED
    task.result_payload = {**(task.result_payload or {}), "skipped": note}
    task.save(update_fields=["status", "result_payload", "updated_at"])


def _now() -> datetime:
    """The clock the approval checks use (a seam for tests)."""
    return timezone.now()


def _latest_decision(action: GrowthAction) -> ApprovalDecision | None:
    """The newest decision on the current version. Uses a prefetch when one was made."""
    version = action.current_version
    if version is None:
        return None
    on_version = [d for d in action.decisions.all() if d.version_id == version.id]
    return max(on_version, key=lambda d: (d.created_at, d.id), default=None)


def approval_expires_at(action: GrowthAction) -> datetime | None:
    """When the approval in force runs out, or None if the action isn't approved."""
    latest = _latest_decision(action)
    if latest is None or latest.decision != ApprovalDecision.Decision.APPROVED:
        return None
    return latest.created_at + APPROVAL_TTL


def approval_expired(action: GrowthAction, now: datetime | None = None) -> bool:
    expires_at = approval_expires_at(action)
    return expires_at is not None and (now or _now()) >= expires_at


def has_valid_approval(action: GrowthAction) -> bool:
    """Whether the newest decision on the current version is an approval, made
    for exactly this content, that has not run out."""
    version = action.current_version
    latest = _latest_decision(action)
    return (
        latest is not None
        and latest.decision == ApprovalDecision.Decision.APPROVED
        and latest.payload_hash == version.payload_hash
        and not approval_expired(action)
    )
