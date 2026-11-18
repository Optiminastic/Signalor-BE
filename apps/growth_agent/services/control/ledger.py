"""
The attempt ledger: pay before you try, and fail closed.

Every send attempt reserves a numbered ticket (an ``attempt`` row) before it
runs. Two workers that pick the same number collide on a unique constraint and
only one goes ahead; the other does nothing. If the ticket can't be written
for any reason, the attempt does not happen.

Each ticket gets a ``result`` row saying how the attempt ended. The number of
consecutive failures is derived from the ledger, never stored:

- the reset point is the newest ``result`` whose outcome resets the count
  (the provider call started, or a legitimate guardrail wait), or the newest
  ``unblock``, whichever is later;
- every ``attempt`` after the reset point counts as a failure until it has a
  resetting result. A ticket with no result counts as a failure.

One failure or more adds a growing wait before the next ticket; at
``MAX_CONSECUTIVE_FAILURES`` the action is blocked until an operator unblocks
it (``growth_unblock_dispatch``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from ...models import DispatchAttemptLedger, GrowthAction

logger = logging.getLogger("apps")

Kind = DispatchAttemptLedger.Kind
MAX_CONSECUTIVE_FAILURES = 10
MAX_BACKOFF_MINUTES = 60

# How an attempt ended.
PROVIDER_STARTED = "provider_started"  # the provider was called (its result is in the trail)
PREFLIGHT_WAIT = "preflight_wait"  # a guardrail said "not now" (pause, kill switch, cap)
CLOSED = "closed"  # the action can't be sent any more (withdrawn, expired, refused, already sent)
LOCK_CONFLICT = "lock_conflict"  # a database deadlock or lock timeout
ERROR = "error"  # anything else before the provider call
RESETTING = frozenset({PROVIDER_STARTED, PREFLIGHT_WAIT, CLOSED})


class AlreadyUnblocked(Exception):
    """There has been no attempt since the last unblock."""


def _now() -> datetime:
    """The clock the ledger uses (a seam for tests)."""
    return timezone.now()


def write(
    action: GrowthAction, kind: str, seq: int, *, outcome: str = "", actor: str = "", reason: str = ""
) -> None:
    DispatchAttemptLedger.objects.create(
        organization_id=action.organization_id,
        action_public_id=action.public_id,
        kind=kind,
        seq=seq,
        outcome=outcome,
        actor=actor,
        reason=reason[:500],
    )


def _rows(public_id: str):
    return DispatchAttemptLedger.objects.filter(action_public_id=public_id)


def _next_seq(public_id: str) -> int:
    latest = _rows(public_id).filter(kind=Kind.ATTEMPT).aggregate(top=Max("seq"))["top"]
    return (latest or 0) + 1


def _reset_point(public_id: str) -> int:
    resets = _rows(public_id).filter(kind=Kind.RESULT, outcome__in=RESETTING).aggregate(top=Max("seq"))["top"]
    unblock = _rows(public_id).filter(kind=Kind.UNBLOCK).aggregate(top=Max("seq"))["top"]
    return max(resets or 0, unblock or 0)


def consecutive_failures(public_id: str) -> int:
    return _rows(public_id).filter(kind=Kind.ATTEMPT, seq__gt=_reset_point(public_id)).count()


def is_blocked(public_id: str) -> bool:
    return consecutive_failures(public_id) >= MAX_CONSECUTIVE_FAILURES


def backoff(failures: int) -> timedelta:
    return timedelta(minutes=min(2 ** (failures - 1), MAX_BACKOFF_MINUTES))


def last_attempt_at(public_id: str):
    last = _rows(public_id).filter(kind=Kind.ATTEMPT).order_by("-seq").first()
    return last.created_at if last else None


def _eligible(public_id: str) -> bool:
    failures = consecutive_failures(public_id)
    if failures >= MAX_CONSECUTIVE_FAILURES:
        logger.error("growth action %s is blocked after %s failed attempts", public_id, failures)
        return False
    if failures == 0:
        return True
    last = _rows(public_id).filter(kind=Kind.ATTEMPT).order_by("-seq").first()
    return _now() >= last.created_at + backoff(failures)


def reserve(action: GrowthAction) -> int | None:
    """Reserve the next ticket for ``action``, or return None: not eligible yet,
    the number was taken by another worker, or the ledger can't be written.
    No ticket means no attempt."""
    try:
        if not _eligible(action.public_id):
            return None
        seq = _next_seq(action.public_id)
        with transaction.atomic():
            write(action, Kind.ATTEMPT, seq)
        return seq
    except IntegrityError:
        return None  # another worker reserved this number; it does the attempt
    except Exception:  # noqa: BLE001 - fail closed: no ticket, no attempt
        logger.exception("could not reserve a send attempt for growth action %s", action.public_id)
        return None


def record_result(action: GrowthAction, seq: int, outcome: str) -> None:
    """Best effort: a ticket left without a result already counts as a failure."""
    try:
        with transaction.atomic():
            write(action, Kind.RESULT, seq, outcome=outcome)
    except Exception:  # noqa: BLE001
        logger.exception(
            "could not record the result of attempt %s for growth action %s", seq, action.public_id
        )


def unblock(action: GrowthAction, *, actor: str, reason: str) -> None:
    """Clear a blocked action: the count restarts and the next ticket is new."""
    seq = _next_seq(action.public_id) - 1
    try:
        with transaction.atomic():
            write(action, Kind.UNBLOCK, seq, actor=actor, reason=reason)
    except IntegrityError as exc:
        raise AlreadyUnblocked(action.public_id) from exc
