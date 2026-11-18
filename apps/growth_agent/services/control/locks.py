"""
The canonical lock order for sending: organization, then program, then cycle,
then the action. Every step that checks a brand-wide limit before a provider call
takes these locks in this order, so two sends of one brand check one at a time
and can't both take the last slot of a daily cap.

Code that locks only an action (decisions, recovery, expiry) is still safe: the
action is last in the order, so no lock cycle can form.
"""

from __future__ import annotations

from django.db.models import Q

from apps.organizations.models import Organization

from ...models import ActionExecutionEvent, GrowthAction, GrowthCycle, GrowthProgram
from ..execution.guardrails import SHARED_CAPS


def lock_brand(action_id: int) -> None:
    """Lock the action's organization, program and cycle rows, in canonical order."""
    ids = (
        GrowthAction.objects.filter(pk=action_id)
        .values("organization_id", "task__cycle__program_id", "task__cycle_id")
        .first()
    )
    if ids is None:
        return
    Organization.objects.select_for_update().filter(pk=ids["organization_id"]).exists()
    GrowthProgram.objects.select_for_update().filter(pk=ids["task__cycle__program_id"]).exists()
    GrowthCycle.objects.select_for_update().filter(pk=ids["task__cycle_id"]).exists()


def in_flight(action: GrowthAction) -> int:
    """Other sends of this brand on channels sharing this action's cap whose provider call
    has started but has no result yet. Call under ``lock_brand``."""
    started = ActionExecutionEvent.objects.filter(event=ActionExecutionEvent.Event.PROVIDER_STARTED)
    finished = ActionExecutionEvent.objects.filter(event=ActionExecutionEvent.Event.PROVIDER_RESULT)
    others = GrowthAction.objects.filter(
        organization_id=action.organization_id,
        state=GrowthAction.State.DISPATCHING,
        task__channel__in=SHARED_CAPS.get(action.task.channel, (action.task.channel,)),
    ).exclude(pk=action.pk)
    count = 0
    for other in others.only("id"):
        last_start = (
            started.filter(action=other).order_by("-attempt").values_list("attempt", flat=True).first()
        )
        if last_start is not None and not finished.filter(Q(action=other) & Q(attempt=last_start)).exists():
            count += 1
    return count
