"""
Management command: publish the daily auto-backlinks batch for every brand whose
schedule is due, then reschedule it +24h.

For each due BacklinkSchedule it resolves the brand's latest completed
AnalysisRun and calls services.backlink_engine.run_auto_backlinks(run), which
generates + publishes one fresh blog to each of the 5 satellite sites.

Usage:   python manage.py run_backlink_schedules [--limit N] [--dry-run]
Cron:    0 * * * * cd /path/to/project && python manage.py run_backlink_schedules
         (hourly; each brand's next_run_at is spread across the day, so every
          brand gets its batch ~24h after its last one.)
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.analyzer.models import AnalysisRun, BacklinkSchedule
from apps.analyzer.services.backlink_engine import run_auto_backlinks

logger = logging.getLogger("apps")


class Command(BaseCommand):
    help = "Publish the daily auto-backlinks batch for every due brand and reschedule +24h."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=25,
            help="Max number of brands to process this tick (default 25).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List due brands without publishing or rescheduling.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        limit = options["limit"]
        dry_run = options["dry_run"]

        due = list(
            BacklinkSchedule.objects.filter(is_active=True, next_run_at__lte=now).order_by(
                "next_run_at"
            )[:limit]
        )

        if not due:
            self.stdout.write("No backlink schedules due.")
            return

        self.stdout.write(f"Found {len(due)} due backlink schedule(s).")

        published = 0
        for sched in due:
            if dry_run:
                self.stdout.write(f"  [dry-run] would run backlinks for {sched.email}")
                continue
            try:
                published += self._run_one(sched)
            except Exception:
                logger.exception("Failed backlink schedule for %s", sched.email)

        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Processed {len(due)} schedule(s); published {published} blog(s)."
                )
            )

    def _run_one(self, sched: BacklinkSchedule) -> int:
        run = self._latest_run_for(sched)
        created = 0
        if run is None:
            logger.warning(
                "backlink schedule %s: no completed AnalysisRun for brand — skipping publish",
                sched.email,
            )
        else:
            result = run_auto_backlinks(run)
            created = len(result.get("created", []))
            sched.run_slug = run.slug
            if result.get("skipped"):
                self.stdout.write(f"  {sched.email}: already published today, skipped")
            else:
                self.stdout.write(f"  {sched.email}: published {created} blog(s)")
            sched.last_batch_count = created

        # Reschedule for tomorrow regardless (missing run / skip both wait a day).
        sched.last_run_at = timezone.now()
        sched.next_run_at = timezone.now() + timedelta(days=1)
        sched.save(
            update_fields=["run_slug", "last_batch_count", "last_run_at", "next_run_at", "updated_at"]
        )
        return created

    @staticmethod
    def _latest_run_for(sched: BacklinkSchedule):
        """Latest completed AnalysisRun for the brand (org preferred, else email)."""
        qs = AnalysisRun.objects.filter(status=AnalysisRun.Status.COMPLETE)
        if sched.organization_id:
            qs = qs.filter(organization_id=sched.organization_id)
        elif sched.email:
            qs = qs.filter(email=sched.email)
        else:
            return None
        return qs.order_by("-created_at").first()
