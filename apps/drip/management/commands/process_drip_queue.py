"""One-shot drip-queue processor.

Useful for manual testing / cron-style schedulers. The long-running embedded
scheduler is `python manage.py runscheduler` — that's what production uses.
"""
from django.core.management.base import BaseCommand

from apps.drip.scheduling import assert_fast_mode_safe
from apps.drip.services import process_drip_queue


class Command(BaseCommand):
    help = "Send the next-due drip email to all eligible non-suppressed users."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--email", type=str, default="")

    def handle(self, *args, **opts):
        assert_fast_mode_safe()
        result = process_drip_queue(dry_run=opts["dry_run"], email_filter=opts["email"])
        self.stdout.write(self.style.SUCCESS(
            f"process_drip_queue: {result.as_dict()} dry_run={opts['dry_run']}"
        ))
