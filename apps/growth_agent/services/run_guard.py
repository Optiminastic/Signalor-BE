"""
Start-and-finish bookkeeping for background checks that log a "started" and a
"done" row (rank check, keyword read).

A run that never logs "done" (the worker or web process restarted mid-run) must
not block the owner: after ``STALE_AFTER`` it no longer counts as running, and
it no longer holds the once-a-day limit.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from ..models import AgentActionLog, GrowthProgram

STALE_AFTER = timedelta(minutes=30)


def _last(program: GrowthProgram, action: str) -> AgentActionLog | None:
    return AgentActionLog.objects.filter(program=program, action=action).order_by("-created_at").first()


def is_running(program: GrowthProgram, started: str, done: str) -> bool:
    """A run started, hasn't finished, and isn't old enough to count as lost."""
    start = _last(program, started)
    if start is None or timezone.now() - start.created_at > STALE_AFTER:
        return False
    finish = _last(program, done)
    return finish is None or finish.created_at < start.created_at


def may_start(program: GrowthProgram, started: str, done: str, guard: timedelta) -> bool:
    """Whether a new run may start: none started within ``guard``, or the last one was lost."""
    start = _last(program, started)
    if start is None or timezone.now() - start.created_at >= guard:
        return True
    finish = _last(program, done)
    finished = finish is not None and finish.created_at >= start.created_at
    lost = not finished and timezone.now() - start.created_at > STALE_AFTER
    return lost
