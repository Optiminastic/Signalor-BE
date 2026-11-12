"""
What happened to an action, in order, for the owner's screen.

Built only from stored records: the draft, each edit (a new version), each
decision, and each step of each send attempt from the append-only trail.
Entries carry roles and reasons, never an email address.
"""

from __future__ import annotations

from datetime import datetime

from rest_framework import serializers

from ...models import GrowthAction
from . import ledger

_STAMP = serializers.DateTimeField()


def _at(moment: datetime) -> str:
    return _STAMP.to_representation(moment)


def _entries(action: GrowthAction) -> list[tuple[datetime, int, dict]]:
    """(time, tiebreak, entry) so entries at the same instant keep a sensible order."""
    rows: list[tuple[datetime, int, dict]] = [(action.created_at, 0, {"type": "drafted", "version": 1})]
    for version in action.versions.all():
        if version.number > 1:
            rows.append((version.created_at, 1, {"type": "edited", "version": version.number}))
    for decision in action.decisions.all():
        entry = {
            "type": "decision",
            "decision": decision.decision,
            "actor_role": decision.actor_role,
            "reason": decision.reason,
            "version": decision.version.number,
        }
        rows.append((decision.created_at, 2, entry))
    for event in action.execution_events.all():
        entry = {
            "type": "send",
            "event": event.event,
            "outcome": event.outcome,
            "attempt": event.attempt,
            "reason": event.detail.get("reason", ""),
        }
        rows.append((event.created_at, 3, entry))
    return rows


def timeline(action: GrowthAction) -> list[dict]:
    rows = sorted(_entries(action), key=lambda row: (row[0], row[1]))
    return [{"at": _at(moment), **entry} for moment, _, entry in rows]


def send_attempts(action: GrowthAction) -> dict:
    """How sending is going: failures in a row, blocked, and the next try."""
    failures = ledger.consecutive_failures(action.public_id)
    blocked = failures >= ledger.MAX_CONSECUTIVE_FAILURES
    retry_after = None
    if failures and not blocked:
        last = ledger.last_attempt_at(action.public_id)
        retry_after = _at(last + ledger.backoff(failures)) if last else None
    return {"failures": failures, "blocked": blocked, "retry_after": retry_after}
