"""
Recovering sends whose worker stopped part-way.

An action stays "being sent" (``dispatching``) if its worker died between the
claim and the result. The trail says what is known:

- Claimed, but ``provider_started`` was never written: the provider was never
  called, so nothing went out. After ``CLAIM_TIMEOUT`` the attempt is closed
  ``aborted`` and the action goes back to approved, to be sent normally later.
- ``provider_started`` with no result: the call may have gone through. After
  ``RESULT_DEADLINE`` the action becomes "outcome unknown". It is never sent
  again automatically; a person decides.

The scheduler runs this before sending, each round.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from ...models import GrowthAction, GrowthTask
from . import actions, dispatch, trail

Event = trail.Event
# The gap between claim and final check is normally milliseconds.
CLAIM_TIMEOUT = timedelta(minutes=5)
# How long a started provider call is given before its outcome counts as unknown.
RESULT_DEADLINE = timedelta(minutes=15)
STOPPED_BEFORE_CALL = "The worker stopped before calling the provider; nothing was sent."
STOPPED_DURING_CALL = "The worker stopped during the provider call; the result is not known."


def _now() -> datetime:
    """The clock recovery uses (a seam for tests)."""
    return timezone.now()


def _put_back(action: GrowthAction, attempt: int) -> None:
    trail.record(action, attempt, Event.ABORTED, reason=STOPPED_BEFORE_CALL)
    action.state = GrowthAction.State.APPROVED
    action.save(update_fields=["state", "updated_at"])
    task = action.task
    task.status = GrowthTask.Status.AWAITING_APPROVAL
    task.save(update_fields=["status", "updated_at"])


def _recover_one(action_id: int, now: datetime) -> bool:
    with transaction.atomic():
        action = (
            actions.lock_actions()
            .select_related("current_version", "task__cycle__program")
            .filter(pk=action_id, state=GrowthAction.State.DISPATCHING)
            .first()
        )
        if action is None:
            return False
        latest = trail.latest_attempt(action)
        if latest is None:
            # Sent before the trail existed: its stage can't be known, so it is
            # treated as possibly sent, once it is clearly stuck.
            if action.updated_at > now - RESULT_DEADLINE:
                return False
            dispatch.park_unknown(action, trail.next_attempt_number(action), STOPPED_DURING_CALL)
            return True
        if latest.provider_started:
            if latest.events[Event.PROVIDER_STARTED] > now - RESULT_DEADLINE:
                return False
            dispatch.park_unknown(action, latest.number, STOPPED_DURING_CALL)
            return True
        if latest.has(Event.CLAIMED) and not latest.has(Event.PROVIDER_STARTED):
            if latest.events[Event.CLAIMED] > now - CLAIM_TIMEOUT:
                return False
            _put_back(action, latest.number)
            return True
        return False


def recover_stuck(now: datetime | None = None) -> int:
    """Recover every send whose worker stopped part-way. Returns how many."""
    moment = now or _now()
    stuck = GrowthAction.objects.filter(state=GrowthAction.State.DISPATCHING).values_list("id", flat=True)
    return sum(_recover_one(action_id, moment) for action_id in stuck)
