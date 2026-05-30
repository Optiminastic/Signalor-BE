"""Production-grade long-running scheduler for the drip queue.

Run locally and in production:
    python manage.py runscheduler

Configure the tick interval via env:
    DRIP_SCHEDULER_INTERVAL_SECONDS=300   # default 300 (5 min)

For testing with compressed delays, combine with:
    DRIP_FAST_MODE=1                       # 60/120/180/240s between emails
    DRIP_SCHEDULER_INTERVAL_SECONDS=20     # tick every 20s
"""
from django.core.management.base import BaseCommand

from apps.drip.scheduler import run_blocking


class Command(BaseCommand):
    help = "Start the long-running APScheduler that ticks the drip queue."

    def handle(self, *args, **opts):
        run_blocking()
