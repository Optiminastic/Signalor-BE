"""
A brand can't be deleted while one of its sends is in flight or its outcome is
still unknown (Phase 1 decision): deleting would erase the only record that a
message or page may exist.

Called from the Organization ``pre_delete`` receiver, inside the deletion's
transaction. It first locks the organization row, the first lock in the
canonical order the sender also takes, so a send's final check and a deletion
are always serialized: either the send sees the brand gone and stops, or the
deletion sees the send in flight and is refused.
"""

from __future__ import annotations

from apps.organizations.exceptions import OrganizationDeletionBlocked
from apps.organizations.models import Organization

from ...models import ActionExecutionEvent, GrowthAction

MESSAGE = (
    "This brand has sends whose result isn't known yet. Resolve them in Approvals "
    "(Needs attention), then delete the brand."
)


def _in_flight(org_id: int) -> list[GrowthAction]:
    """Dispatching actions whose provider call started and has no result yet."""
    flying = []
    for action in GrowthAction.objects.filter(organization_id=org_id, state=GrowthAction.State.DISPATCHING):
        events = ActionExecutionEvent.objects.filter(action=action)
        started = (
            events.filter(event=ActionExecutionEvent.Event.PROVIDER_STARTED).order_by("-attempt").first()
        )
        if (
            started
            and not events.filter(
                event=ActionExecutionEvent.Event.PROVIDER_RESULT, attempt=started.attempt
            ).exists()
        ):
            flying.append(action)
    return flying


def _describe(action: GrowthAction) -> dict:
    return {
        "action": action.public_id,
        "kind": action.kind,
        "title": action.task.title,
        "since": action.updated_at.isoformat(),
    }


def guard(org_id: int) -> None:
    """Lock the brand and refuse its deletion while anything is unresolved."""
    Organization.objects.select_for_update().filter(pk=org_id).exists()
    flying = _in_flight(org_id)
    waiting = list(
        GrowthAction.objects.select_related("task").filter(
            organization_id=org_id, state=GrowthAction.State.NEEDS_RECONCILIATION
        )
    )
    if not flying and not waiting:
        return
    raise OrganizationDeletionBlocked(
        {
            "detail": MESSAGE,
            "in_flight": len(flying),
            "awaiting_reconciliation": [_describe(a) for a in waiting],
        }
    )
