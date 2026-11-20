"""
One brand's round of Growth Agent work ("brand tick"), run on the background
queue once per scheduler round for every active brand.

- Drafts are written ahead (``PREPARE_AHEAD``); sends wait for each task's day.
- A fair share per round: at most ``PREPARES_PER_TICK`` drafts and
  ``SENDS_PER_TICK`` sends, oldest first; the rest waits for the next round, so
  no brand can take a whole round.
- One tick per brand at a time: a Redis lock (``cache.add``, atomic) with a TTL
  longer than the tick's time limit; an overlapping tick for the same brand skips.
- Each step is isolated: a failing check is logged and the next one still runs,
  and a failing brand never touches another brand's tick (separate tasks).
- Finishing stamps ``GrowthProgram.last_tick_at``, the heartbeat the health
  check watches.

Sends keep every Phase 1 protection: ``dispatch`` claims, re-checks the approval,
the brand locks and the caps, exactly as before.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from ...models import GrowthProgram
from ..control.dispatch import dispatch, dispatchable
from ..execution.executor import due_tasks, run_task

logger = logging.getLogger("apps")

PREPARES_PER_TICK = 5
# Drafts are written ahead, for tasks due within this window, so the owner has
# drafts to review before each task's day. Sending still waits for the task's
# day (``dispatchable``) and the owner's approval. Shorter than the approval TTL
# (14 days), so an early approval can't expire before its send day.
PREPARE_AHEAD = timedelta(days=10)
SENDS_PER_TICK = 5
# Longer than the brand_tick time limit (20 min), so a crashed worker frees it eventually.
LOCK_SECONDS = 25 * 60


@dataclass
class TickResult:
    prepared: int = 0
    sent: int = 0
    failed_steps: list[str] = field(default_factory=list)


def _lock_key(program_id: int) -> str:
    return f"growth:tick:{program_id}"


def _periodic_steps(program: GrowthProgram) -> list[tuple[str, object]]:
    """The brand's daily and weekly checks, in order. Imported here: they pull in
    heavy modules the scheduler process doesn't need."""
    from .. import weekly_summary  # noqa: PLC0415
    from ..guest_posts import finder as guest_finder  # noqa: PLC0415
    from ..indexing.weekly import weekly_round  # noqa: PLC0415
    from ..keywords import refresh as keyword_refresh  # noqa: PLC0415
    from ..outreach.follow_up_actions import prepare_due_follow_ups  # noqa: PLC0415
    from ..outreach.followups import process_open_threads  # noqa: PLC0415
    from ..planning.plan import add_satellite_topics  # noqa: PLC0415
    from ..rank_tracking import check as rank_check  # noqa: PLC0415
    from ..site_watch import watch as site_watch  # noqa: PLC0415
    from ..social import check as social_check  # noqa: PLC0415
    from ..social import posts as social_posts  # noqa: PLC0415

    today = timezone.now().date()
    return [
        ("replies", lambda: process_open_threads(program)),
        ("follow_ups", lambda: prepare_due_follow_ups(today, program)),
        ("satellite_topics", lambda: add_satellite_topics(program)),
        ("indexing", lambda: weekly_round(program)),
        ("social_check", lambda: social_check.weekly(program)),
        ("guest_sites", lambda: guest_finder.weekly(program)),
        ("keywords", lambda: keyword_refresh.weekly(program)),
        ("site_watch", lambda: site_watch.daily(program)),
        ("ranks", lambda: rank_check.weekly(program)),
        ("social_posts", lambda: social_posts.queue_weekly(program)),
        ("weekly_summary", lambda: weekly_summary.post(program)),
    ]


def _step(result: TickResult, program: GrowthProgram, name: str, call) -> None:
    try:
        call()
    except Exception:  # noqa: BLE001 - one failing step must not stop the brand's others
        logger.exception("growth tick: step %s failed for program %s", name, program.pk)
        result.failed_steps.append(name)


def _work(program: GrowthProgram, result: TickResult) -> None:
    today = timezone.now().date()
    for task in list(due_tasks(today + PREPARE_AHEAD).filter(cycle__program=program)[:PREPARES_PER_TICK]):
        _step(result, program, f"prepare:{task.id}", lambda t=task: run_task(t.id))
        result.prepared += 1
    for action in list(dispatchable(today).filter(task__cycle__program=program)[:SENDS_PER_TICK]):
        _step(result, program, f"send:{action.public_id}", lambda a=action: dispatch(a.id))
        result.sent += 1
    for name, call in _periodic_steps(program):
        _step(result, program, name, call)


def run(program_id: int) -> TickResult | None:
    """Do one brand's round. None when skipped (another tick holds the brand, or it isn't running)."""
    key, token = _lock_key(program_id), uuid.uuid4().hex
    if not cache.add(key, token, LOCK_SECONDS):
        logger.info("growth tick: program %s is already being worked on; skipping", program_id)
        return None
    try:
        program = (
            GrowthProgram.objects.select_related("organization")
            .filter(pk=program_id, status=GrowthProgram.Status.ACTIVE, kill_switch=False)
            .first()
        )
        if program is None:
            return None
        result = TickResult()
        _work(program, result)
        GrowthProgram.objects.filter(pk=program_id).update(last_tick_at=timezone.now())
        return result
    finally:
        if cache.get(key) == token:
            cache.delete(key)
