"""
The Growth Agent scheduler: one long-running process (the ``growth-scheduler``
compose service) that runs a round every GROWTH_SCHEDULER_INTERVAL_SECONDS
(default 15 minutes) and then checks health.

    python manage.py run_growth_scheduler

Run exactly one per deployment, never inside the web workers. A second copy is
harmless: each round takes a Redis lock, so overlapping rounds skip.
"""

import logging
import signal
from datetime import UTC, datetime

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.growth_agent.services.scheduling import health
from apps.growth_agent.services.scheduling.round import run_round

logger = logging.getLogger("apps")
MIN_INTERVAL_SECONDS = 60


def _round() -> None:
    close_old_connections()
    try:
        result = run_round()
        logger.info("growth round: %s", result)
    except Exception:  # noqa: BLE001 - one bad round must not kill the scheduler
        logger.exception("growth round crashed (next round will retry)")
    try:
        health.alert(health.problems())
    except Exception:  # noqa: BLE001
        logger.exception("growth health check crashed")
    finally:
        close_old_connections()


class Command(BaseCommand):
    help = "Run the Growth Agent scheduler (one round every 15 minutes)."

    def handle(self, *args, **options):
        interval = max(MIN_INTERVAL_SECONDS, int(settings.GROWTH_SCHEDULER_INTERVAL_SECONDS))
        scheduler = BlockingScheduler(executors={"default": ThreadPoolExecutor(1)}, timezone="UTC")
        scheduler.add_job(
            _round,
            "interval",
            seconds=interval,
            id="growth_round",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(UTC),  # first round right away, not after one interval
        )

        def stop(*_):
            scheduler.shutdown(wait=False)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        self.stdout.write(f"Growth scheduler: a round every {interval}s.")
        scheduler.start()
