"""
Approvals that run out.

An approval counts for ``approvals.APPROVAL_TTL``. An approved action whose
approval has run out becomes ``expired`` and is never sent; the owner would
have to approve a new draft. The sender checks this itself before every send
(``dispatch._claim``), and the scheduler also sweeps for expired approvals so
they don't sit as "approved" on the owner's screen.

An expiry is recorded as an immutable decision with the system as the actor.
"""

from __future__ import annotations

from datetime import datetime

from django.db import transaction

from ...models import AgentActionLog, ApprovalDecision, GrowthAction
from ..execution import audit
from . import actions, approvals

ROLE_SYSTEM = "system"
EXPIRED_NOTE = "The approval expired before it was sent."
AUDIT_EXPIRED = "approval_expired"


def expire_locked(action: GrowthAction) -> None:
    """Expire ``action``. The caller holds its row lock inside a transaction."""
    ApprovalDecision.objects.create(
        organization=action.organization,
        action=action,
        version=action.current_version,
        payload_hash=action.current_version.payload_hash,
        decision=ApprovalDecision.Decision.EXPIRED,
        actor_email="",
        actor_role=ROLE_SYSTEM,
    )
    action.state = GrowthAction.State.EXPIRED
    action.save(update_fields=["state", "updated_at"])
    if actions.updates_task(action):
        approvals.skip_task(action.task, EXPIRED_NOTE)
    audit.record(
        action.task.cycle.program,
        action.task,
        AUDIT_EXPIRED,
        AgentActionLog.Outcome.BLOCKED,
        {"action": action.public_id, "version": action.current_version.number},
    )


def _expire_if_due(action_id: int, now: datetime | None) -> bool:
    with transaction.atomic():
        action = (
            actions.lock_actions()
            .select_related("current_version", "task__cycle__program")
            .filter(pk=action_id, state=GrowthAction.State.APPROVED)
            .first()
        )
        if action is None or not approvals.approval_expired(action, now):
            return False
        expire_locked(action)
        return True


def expire_due(now: datetime | None = None) -> int:
    """Expire every approved action whose approval has run out. Returns how many."""
    candidates = GrowthAction.objects.filter(state=GrowthAction.State.APPROVED).values_list("id", flat=True)
    return sum(_expire_if_due(action_id, now) for action_id in candidates)
