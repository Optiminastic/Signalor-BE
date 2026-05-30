"""Long-running APScheduler that ticks the drip queue on a fixed interval.

Started via `python manage.py runscheduler`. Deploy as its own process — do
NOT run inside the gunicorn worker pool (every worker would spawn its own
scheduler and you'd send each email N times).

Single-instance safety: APScheduler's `max_instances=1` + `coalesce=True`
prevent the same job from running twice concurrently within one process. To
scale beyond one scheduler process, swap the in-memory job store for the
SQLAlchemy job store backed by Postgres — but for normal traffic, one process
is fine and much simpler.
"""
import logging
import os
import signal

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.blocking import BlockingScheduler
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .scheduling import assert_fast_mode_safe
from .services import process_drip_queue

logger = logging.getLogger("apps")

JOB_ID = "drip_queue_tick"
DEFAULT_INTERVAL_SECONDS = 300  # 5 minutes
# Health-check cache key — `check_drip_health` reads this. The TTL is set to
# 10x the tick interval so a missed tick is still detectable as freshness loss
# rather than as "key missing" from cache eviction.
HEARTBEAT_CACHE_KEY = "drip_scheduler:last_tick"
HEARTBEAT_TTL_SECONDS = 60 * 60  # 1h; far longer than any healthy tick interval


def _interval_seconds() -> int:
    raw = os.getenv("DRIP_SCHEDULER_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS))
    try:
        return max(10, int(raw))
    except ValueError:
        logger.warning("Invalid DRIP_SCHEDULER_INTERVAL_SECONDS=%r, falling back to %s",
                       raw, DEFAULT_INTERVAL_SECONDS)
        return DEFAULT_INTERVAL_SECONDS


def _tick():
    """Called by APScheduler on every interval. Failures are caught here so
    one bad cycle doesn't poison the scheduler. Writes a heartbeat into cache
    so `check_drip_health` can detect a stuck/dead scheduler externally."""
    try:
        result = process_drip_queue()
        logger.info("drip tick: %s", result.as_dict())
    except Exception:
        logger.exception("drip tick crashed (will retry on next interval)")
    # Heartbeat is written even on crash: a crashed cycle that still ran proves
    # the scheduler loop itself is alive — the bug is in the work, not the host.
    try:
        cache.set(HEARTBEAT_CACHE_KEY, timezone.now().isoformat(), HEARTBEAT_TTL_SECONDS)
    except Exception:
        logger.exception("Failed to write drip heartbeat to cache")


def build_scheduler() -> BlockingScheduler:
    """Wire a BlockingScheduler with sane production defaults."""
    scheduler = BlockingScheduler(
        jobstores={"default": MemoryJobStore()},
        executors={"default": ThreadPoolExecutor(max_workers=4)},
        job_defaults={
            "coalesce": True,         # piled-up missed runs collapse into one
            "max_instances": 1,       # never two ticks at once
            "misfire_grace_time": 60, # tolerate 60s of scheduler lag before dropping
        },
        timezone=getattr(settings, "TIME_ZONE", "UTC"),
    )

    interval = _interval_seconds()
    scheduler.add_job(
        _tick,
        trigger="interval",
        seconds=interval,
        next_run_time=timezone.now(),  # tick once immediately on start
        id=JOB_ID,
        replace_existing=True,
    )
    logger.info("drip scheduler configured: interval=%ds", interval)
    return scheduler


def run_blocking():
    """Start the scheduler in the foreground. Returns when the process gets
    SIGINT / SIGTERM; in-flight ticks are allowed to finish."""
    assert_fast_mode_safe()
    scheduler = build_scheduler()

    def _shutdown(signum, _frame):
        logger.info("drip scheduler received signal %s, shutting down gracefully", signum)
        scheduler.shutdown(wait=True)

    signal.signal(signal.SIGINT, _shutdown)
    # SIGTERM isn't supported on native Windows but is on Linux + WSL + containers.
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except (AttributeError, ValueError):
        pass

    logger.info("drip scheduler starting...")
    scheduler.start()
    logger.info("drip scheduler stopped cleanly")
