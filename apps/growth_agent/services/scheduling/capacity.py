"""
How many brands the Growth Agent runs at once (``GROWTH_MAX_ACTIVE_PROGRAMS``,
default 50, the size the workers and the load test are built for).

Checked when a brand would become active (its first plan, or resuming after a
pause), under one Postgres advisory lock held to the end of the transaction, so
two brands starting at the same moment can't both take the last place.
"""

from __future__ import annotations

from django.conf import settings
from django.db import connection

from ...models import GrowthProgram
from ..planning.plan import PlanError

DEFAULT_MAX_ACTIVE = 50
# Arbitrary constant naming the "active brands" lock (pg advisory locks take a bigint).
ACTIVE_LOCK_KEY = 0x6772_6F77_7468  # "growth"
FULL_MESSAGE = (
    "The Growth Agent is running at full capacity right now, so this brand can't start yet. "
    "It stays set up and will start as soon as a place frees up."
)


class CapacityReached(PlanError):
    """No place for another active brand. A PlanError, so callers already handle it."""


def max_active() -> int:
    return int(getattr(settings, "GROWTH_MAX_ACTIVE_PROGRAMS", DEFAULT_MAX_ACTIVE))


def _lock() -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [ACTIVE_LOCK_KEY])


def ensure_place(program: GrowthProgram) -> None:
    """Raise CapacityReached unless ``program`` may become active. Call inside the
    transaction that activates it (the lock lasts until that commits)."""
    _lock()
    others = GrowthProgram.objects.filter(status=GrowthProgram.Status.ACTIVE).exclude(pk=program.pk).count()
    if others >= max_active():
        raise CapacityReached(FULL_MESSAGE)
