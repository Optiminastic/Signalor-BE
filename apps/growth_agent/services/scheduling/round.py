"""
One scheduler round (every 15 minutes, from ``run_growth_scheduler`` or the
``run_growth_tasks`` cron command).

The round itself only does quick, database-only housekeeping, then queues one
``brand_tick`` per active brand on the background queue. All slow work (drafts,
sends, mailbox reads, weekly checks) happens inside the ticks, on workers, so a
round takes seconds whatever the number of brands, and a slow brand only delays
itself. Without a broker (local dev, tests) the ticks run inline, one by one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.core.cache import cache
from django.utils import timezone

from ...models import GrowthProgram
from ..control.expiry import expire_due
from ..control.recovery import recover_stuck
from ..execution.executor import release_stale_executions

logger = logging.getLogger("apps")

ROUND_LOCK_KEY = "growth:round"
ROUND_LOCK_SECONDS = 10 * 60
HEARTBEAT_KEY = "growth_scheduler:last_round"
HEARTBEAT_TTL_SECONDS = 24 * 60 * 60


@dataclass
class RoundResult:
    skipped: bool = False
    released: int = 0
    recovered: int = 0
    expired: int = 0
    ticks: int = 0


def active_programs():
    return GrowthProgram.objects.filter(status=GrowthProgram.Status.ACTIVE, kill_switch=False).order_by(
        "last_tick_at", "id"
    )


def _advance_onboarding() -> None:
    from ..autopilot import advance_onboarding  # noqa: PLC0415  (pulls in the planner)

    for program in GrowthProgram.objects.filter(status=GrowthProgram.Status.ONBOARDING):
        try:
            advance_onboarding(program)
        except Exception:  # noqa: BLE001 - one brand's onboarding must not stop the round
            logger.exception("growth round: onboarding step failed for program %s", program.pk)


def _queue_ticks(program_ids: list[int]) -> None:
    from ...analysis_tasks import dispatch_brand_tick  # noqa: PLC0415  (avoids an import cycle)

    for program_id in program_ids:
        dispatch_brand_tick(program_id)


def run_round() -> RoundResult:
    """Housekeeping, then one tick per active brand. Skips if another round is running."""
    if not cache.add(ROUND_LOCK_KEY, timezone.now().isoformat(), ROUND_LOCK_SECONDS):
        return RoundResult(skipped=True)
    try:
        result = RoundResult()
        _advance_onboarding()
        result.released = release_stale_executions()
        result.recovered = recover_stuck()
        result.expired = expire_due()
        ids = list(active_programs().values_list("id", flat=True))
        _queue_ticks(ids)
        result.ticks = len(ids)
        cache.set(HEARTBEAT_KEY, timezone.now().isoformat(), HEARTBEAT_TTL_SECONDS)
        return result
    finally:
        cache.delete(ROUND_LOCK_KEY)
