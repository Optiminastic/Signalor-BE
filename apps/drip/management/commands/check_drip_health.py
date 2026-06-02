"""Health probe for the drip scheduler. Exit 0 if the scheduler ticked
recently, exit 1 otherwise. Wire to your platform's cron monitor / liveness
check (Render Health Checks, Heroku scheduler, etc.).

Usage:
    python manage.py check_drip_health
    python manage.py check_drip_health --max-age-seconds 900
"""
import sys
from datetime import datetime, timedelta, timezone as dt_tz

from django.core.cache import cache
from django.core.management.base import BaseCommand

from apps.drip.scheduler import HEARTBEAT_CACHE_KEY


class Command(BaseCommand):
    help = "Exit 1 if the drip scheduler hasn't written a heartbeat recently."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-age-seconds",
            type=int,
            default=900,  # 15 min — 3x the default 5-min tick interval
            help="How stale the last heartbeat may be before this command fails.",
        )

    def handle(self, *args, **opts):
        raw = cache.get(HEARTBEAT_CACHE_KEY)
        if not raw:
            self.stderr.write(self.style.ERROR(
                "drip scheduler heartbeat missing — has runscheduler ever run on this instance?"
            ))
            sys.exit(1)

        try:
            last = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            self.stderr.write(self.style.ERROR(f"heartbeat value malformed: {raw!r}"))
            sys.exit(1)

        if last.tzinfo is None:
            last = last.replace(tzinfo=dt_tz.utc)
        now = datetime.now(dt_tz.utc)
        age = now - last

        if age > timedelta(seconds=opts["max_age_seconds"]):
            self.stderr.write(self.style.ERROR(
                f"drip scheduler heartbeat is stale: last tick {age.total_seconds():.0f}s ago "
                f"(threshold {opts['max_age_seconds']}s) at {last.isoformat()}"
            ))
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS(
            f"drip scheduler healthy — last tick {age.total_seconds():.0f}s ago at {last.isoformat()}"
        ))
