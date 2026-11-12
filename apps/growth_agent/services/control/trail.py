"""
The append-only trail of each attempt to send an action.

``ActionExecutionEvent`` rows are only ever added. This module writes them and
answers the questions the sender, revocation and crash recovery ask of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ...models import ActionExecutionEvent, GrowthAction

Event = ActionExecutionEvent.Event
DETAIL_LIMIT = 500


@dataclass(frozen=True)
class Attempt:
    """What is known about an action's latest attempt."""

    number: int
    events: dict[str, datetime]

    def has(self, event: str) -> bool:
        return event in self.events

    @property
    def provider_started(self) -> bool:
        return self.has(Event.PROVIDER_STARTED) and not self.has(Event.PROVIDER_RESULT)


def latest_attempt(action: GrowthAction) -> Attempt | None:
    rows = ActionExecutionEvent.objects.filter(action=action).order_by("-attempt", "created_at", "id")
    first = rows.first()
    if first is None:
        return None
    events = {row.event: row.created_at for row in rows.filter(attempt=first.attempt)}
    return Attempt(number=first.attempt, events=events)


def next_attempt_number(action: GrowthAction) -> int:
    latest = latest_attempt(action)
    return latest.number + 1 if latest else 1


def provider_call_in_progress(action: GrowthAction) -> bool:
    """Whether the provider may be handling this action right now."""
    latest = latest_attempt(action)
    return latest is not None and latest.provider_started


def record(action: GrowthAction, attempt: int, event: str, *, outcome: str = "", reason: str = "") -> None:
    ActionExecutionEvent.objects.create(
        organization=action.organization,
        action=action,
        version=action.current_version,
        attempt=attempt,
        event=event,
        outcome=outcome,
        detail={"reason": reason[:DETAIL_LIMIT]} if reason else {},
    )
