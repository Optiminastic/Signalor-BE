"""
Take the weekly visibility snapshot for every active Growth Agent cycle that is due.

Run from cron (hourly is fine; a week's snapshot is only ever taken once):
    python manage.py run_growth_tracking [--dry-run]

With a RabbitMQ broker the snapshots are queued on the analysis worker. Without
one (local dev) they run inline, because a background thread would die with
this short-lived process.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.growth_agent.analysis_tasks import run_weekly_snapshot_task
from apps.growth_agent.models import GrowthCycle, GrowthProgram
from apps.growth_agent.services.tracking.tracker import elapsed_week, request_scheduled_snapshot, run_snapshot
from config.celery_rabbit import analysis_app


class Command(BaseCommand):
    help = "Take due weekly AI-visibility snapshots for active Growth Agent cycles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="List due cycles without re-checking anything."
        )

    def handle(self, *args, **options):
        today = timezone.now().date()
        cycles = GrowthCycle.objects.filter(
            status=GrowthCycle.Status.ACTIVE,
            program__status=GrowthProgram.Status.ACTIVE,
            program__kill_switch=False,
            starts_on__lte=today,
        ).select_related("program__organization")
        started = 0
        for cycle in cycles:
            if options["dry_run"]:
                self.stdout.write(
                    f"cycle {cycle.id} ({cycle.program.organization.name}): week {elapsed_week(cycle, today)}"
                )
                continue
            snapshot = request_scheduled_snapshot(cycle, today)
            if snapshot is None:
                continue
            started += 1
            if analysis_app.conf.task_always_eager:
                run_snapshot(snapshot.id)
            else:
                run_weekly_snapshot_task.delay(snapshot.id)
        self.stdout.write(self.style.SUCCESS(f"Started {started} snapshot(s)."))
