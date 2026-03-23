"""
Management command to run due scheduled analyses and send email digests.
Usage: python manage.py run_scheduled_analyses
Trigger via cron: */30 * * * * cd /path/to/project && python manage.py run_scheduled_analyses
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.analyzer.models import AnalysisRun, ScheduledAnalysis
from apps.analyzer.tasks import run_single_page_analysis
from apps.analyzer.email_utils import send_digest_email

logger = logging.getLogger("apps")


class Command(BaseCommand):
    help = "Run due scheduled analyses and send email digests"

    def handle(self, *args, **options):
        now = timezone.now()
        due = ScheduledAnalysis.objects.filter(is_active=True, next_run_at__lte=now)
        count = due.count()

        if count == 0:
            self.stdout.write("No scheduled analyses due.")
            return

        self.stdout.write(f"Found {count} due scheduled analyses.")

        for schedule in due:
            try:
                self._run_one(schedule)
            except Exception:
                logger.exception(f"Failed scheduled analysis for {schedule.email}")

        self.stdout.write(self.style.SUCCESS(f"Processed {count} scheduled analyses."))

    def _run_one(self, schedule: ScheduledAnalysis):
        # Get previous score for comparison
        prev_run = (
            AnalysisRun.objects.filter(
                organization=schedule.organization,
                status="complete",
            )
            .order_by("-created_at")
            .first()
        )
        prev_score = prev_run.composite_score if prev_run else None

        # Create new analysis run
        run = AnalysisRun.objects.create(
            organization=schedule.organization,
            url=schedule.url,
            email=schedule.email,
            brand_name=schedule.brand_name,
            run_type="single_page",
            status="pending",
        )

        self.stdout.write(f"  Running analysis for {schedule.url} (run {run.id})...")
        run_single_page_analysis(run.id)

        # Refresh from DB
        run.refresh_from_db()

        # Update schedule
        delta = timedelta(days=7) if schedule.frequency == "weekly" else timedelta(days=30)
        schedule.last_run_at = timezone.now()
        schedule.last_run_slug = run.slug
        schedule.next_run_at = timezone.now() + delta
        schedule.save(update_fields=["last_run_at", "last_run_slug", "next_run_at"])

        # Send email digest
        if run.status == "complete":
            score_change = None
            if prev_score is not None and run.composite_score is not None:
                score_change = round(run.composite_score - prev_score, 1)

            top_recs = list(
                run.recommendations.order_by("priority")[:3].values("title", "priority", "category")
            )

            send_digest_email(
                to_email=schedule.email,
                context={
                    "brand_name": schedule.brand_name or schedule.url,
                    "url": schedule.url,
                    "score": round(run.composite_score or 0),
                    "score_change": score_change,
                    "prev_score": round(prev_score) if prev_score else None,
                    "recommendations": top_recs,
                    "slug": run.slug,
                },
            )
            self.stdout.write(f"  Digest email sent to {schedule.email}")
