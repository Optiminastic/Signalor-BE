"""Dispatch due scheduled analyses to the analysis worker.

Usage: python manage.py run_scheduled_analyses
Cron:  */30 * * * * cd /path/to/project && python manage.py run_scheduled_analyses

This command only *dispatches*. The work itself — re-scan, task sync, digest —
runs on the RabbitMQ analysis worker via ``analyzer.run_scheduled_analysis``
(see apps/analyzer/scheduled_runs.py).

It used to call ``run_single_page_analysis`` inline, serially, for every due
brand, inside a cron container with a runtime cap. One slow brand starved the
rest and a timeout killed the batch mid-way, leaving the survivors unrun. Now the
cron finishes in milliseconds and each brand fails independently.

Claiming happens inside the task, not here: this command can overlap with itself
(a tick that outlives its 30-minute window) and the queue is at-least-once, so
the claim has to be where the work is.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.analyzer.models import ScheduledAnalysis

logger = logging.getLogger("apps")


class Command(BaseCommand):
    help = "Dispatch due scheduled analyses to the analysis worker."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what is due without dispatching.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        due = list(
            ScheduledAnalysis.objects.filter(is_active=True, next_run_at__lte=now).only("id", "email", "url")
        )

        if not due:
            self.stdout.write("No scheduled analyses due.")
            return

        if options["dry_run"]:
            for schedule in due:
                self.stdout.write(f"  would dispatch schedule {schedule.id} ({schedule.url})")
            self.stdout.write(self.style.SUCCESS(f"{len(due)} due. Nothing dispatched."))
            return

        dispatched = 0
        for schedule in due:
            try:
                self._dispatch(schedule.id)
                dispatched += 1
            except Exception:
                logger.exception("failed to dispatch scheduled analysis %s", schedule.id)
                self.stderr.write(f"  dispatch failed for schedule {schedule.id}")

        self.stdout.write(self.style.SUCCESS(f"Dispatched {dispatched} of {len(due)} due analyses."))

    def _dispatch(self, schedule_id: int) -> None:
        """Queue the run, or execute inline when there's no broker (dev / tests)."""
        from apps.analyzer.analysis_tasks import run_scheduled_analysis_task
        from config.celery_rabbit import analysis_app

        if analysis_app.conf.task_always_eager:
            from apps.analyzer.scheduled_runs import execute_scheduled_analysis

            execute_scheduled_analysis(schedule_id)
        else:
            run_scheduled_analysis_task.delay(schedule_id)
