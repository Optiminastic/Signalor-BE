"""
Review batches: the brand's drafts grouped by day, so the owner can reject
several at once.

A batch never approves. Approval stays one decision per action and exact
version (Phase 1 decision). Rejecting through a batch runs the ordinary
single-action decision for each item, each in its own transaction, with its own
version check, decision record and audit entry; one failing item never affects
another. An item whose version is no longer the action's current one is
superseded and can't be decided.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...models import ApprovalBatch, ApprovalBatchItem, ApprovalDecision, GrowthProgram
from . import approvals

REQUIRES_INDIVIDUAL = "requires_individual"
REJECTED = "rejected"
SUPERSEDED = "superseded"
NOT_FOUND = "not_found"
REFUSAL_RESULTS = {"stale": "stale", "illegal_state": "illegal_state", approvals.IN_FLIGHT: "illegal_state"}


@dataclass(frozen=True)
class BatchItemRequest:
    action_id: str
    version_number: int
    payload_hash: str


def batches_for(program: GrowthProgram):
    return ApprovalBatch.objects.filter(organization=program.organization)


def _item_result(
    program: GrowthProgram, batch: ApprovalBatch, item: BatchItemRequest, actor: str, reason: str
) -> str:
    entry = (
        ApprovalBatchItem.objects.select_related("action__current_version", "version")
        .filter(batch=batch, action__public_id=item.action_id, version__number=item.version_number)
        .first()
    )
    if entry is None:
        return NOT_FOUND
    if entry.action.current_version_id != entry.version_id:
        return SUPERSEDED
    request = approvals.DecisionRequest(
        decision=ApprovalDecision.Decision.REJECTED,
        version_number=item.version_number,
        payload_hash=item.payload_hash,
        actor_email=actor,
        reason=reason,
    )
    try:
        approvals.decide(program, item.action_id, request)
    except approvals.ActionNotFound:
        return NOT_FOUND
    except approvals.DecisionRefused as exc:
        return REFUSAL_RESULTS.get(exc.code, "illegal_state")
    return REJECTED


def reject(
    program: GrowthProgram,
    batch: ApprovalBatch,
    items: list[BatchItemRequest],
    *,
    actor_email: str,
    reason: str = "",
) -> list[dict]:
    """Reject each item independently. Returns one result per item, in order."""
    return [
        {
            "action_id": item.action_id,
            "version": item.version_number,
            "result": _item_result(program, batch, item, actor_email, reason),
        }
        for item in items
    ]
