"""
Execute every due Growth Agent task autonomously, then check outreach threads
for replies and send due follow-ups.

Run from cron, e.g. every 15 minutes:
    python manage.py run_growth_tasks [--limit N] [--dry-run]

Tasks are claimed with a row lock, so overlapping cron runs never double-run a
task. With a RabbitMQ broker tasks are queued on the analysis worker; without
one (local dev) they run inline in this process.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.growth_agent.analysis_tasks import run_growth_task
from apps.growth_agent.services.execution.executor import due_tasks, release_stale_executions, run_task
from apps.growth_agent.services.outreach.followups import process_open_threads
from config.celery_rabbit import analysis_app

DEFAULT_LIMIT = 50


class Command(BaseCommand):
    help = "Run due Growth Agent tasks (autonomous off-page execution)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max tasks to start this run.")
        parser.add_argument("--dry-run", action="store_true", help="List due tasks without running them.")

    def handle(self, *args, **options):
        released = release_stale_executions()
        if released:
            self.stdout.write(f"Re-queued {released} stale task(s).")
        tasks = list(due_tasks(timezone.now().date())[: options["limit"]])
        for task in tasks:
            if options["dry_run"]:
                self.stdout.write(f"task {task.id} [{task.channel}] {task.title}")
            elif analysis_app.conf.task_always_eager:
                run_task(task.id)
            else:
                run_growth_task.delay(task.id)
        verb = "Due" if options["dry_run"] else "Started"
        self.stdout.write(self.style.SUCCESS(f"{verb}: {len(tasks)} task(s)."))
        if not options["dry_run"]:
            threads = process_open_threads()
            self.stdout.write(f"Checked {threads} outreach thread(s) for replies and follow-ups.")
