"""
One round of Growth Agent work:

1. Prepare a draft for every due task. Nothing is sent; each draft waits for
   the brand owner's approval.
2. Send every action the owner has approved.
3. Check outreach threads for replies.

Run from cron, e.g. every 15 minutes:
    python manage.py run_growth_tasks [--limit N] [--dry-run]

Tasks and actions are claimed with a row lock, so overlapping cron runs never
prepare or send the same thing twice. With a RabbitMQ broker the work is queued
on the worker; without one (local dev) it runs inline in this process.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.growth_agent.analysis_tasks import dispatch_action_task, run_growth_task
from apps.growth_agent.services.control.dispatch import dispatch, dispatchable
from apps.growth_agent.services.control.expiry import expire_due
from apps.growth_agent.services.control.recovery import recover_stuck
from apps.growth_agent.services.execution.executor import due_tasks, release_stale_executions, run_task
from apps.growth_agent.services.outreach.followups import process_open_threads
from config.celery_rabbit import analysis_app

DEFAULT_LIMIT = 50


class Command(BaseCommand):
    help = "Prepare due Growth Agent tasks and send the actions the owner approved."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max tasks to start this run.")
        parser.add_argument("--dry-run", action="store_true", help="List due tasks without running them.")

    def handle(self, *args, **options):
        released = release_stale_executions()
        if released:
            self.stdout.write(f"Re-queued {released} stale task(s).")
        today = timezone.now().date()
        tasks = list(due_tasks(today)[: options["limit"]])
        for task in tasks:
            if options["dry_run"]:
                self.stdout.write(f"task {task.id} [{task.channel}] {task.title}")
            elif analysis_app.conf.task_always_eager:
                run_task(task.id)
            else:
                run_growth_task.delay(task.id)
        verb = "Due" if options["dry_run"] else "Preparing"
        self.stdout.write(self.style.SUCCESS(f"{verb}: {len(tasks)} task(s)."))
        if not options["dry_run"]:
            recovered = recover_stuck()
            if recovered:
                self.stdout.write(f"Recovered {recovered} send(s) whose worker stopped part-way.")
            expired = expire_due()
            if expired:
                self.stdout.write(f"Expired {expired} approval(s) older than the approval window.")
        approved = list(dispatchable(today)[: options["limit"]])
        for action in approved:
            if options["dry_run"]:
                self.stdout.write(f"approved action {action.public_id} [{action.kind}] {action.task.title}")
            elif analysis_app.conf.task_always_eager:
                dispatch(action.id)
            else:
                dispatch_action_task.delay(action.id)
        verb = "Approved and due" if options["dry_run"] else "Sending"
        self.stdout.write(self.style.SUCCESS(f"{verb}: {len(approved)} action(s)."))
        if not options["dry_run"]:
            threads = process_open_threads()
            self.stdout.write(f"Checked {threads} outreach thread(s) for replies.")
