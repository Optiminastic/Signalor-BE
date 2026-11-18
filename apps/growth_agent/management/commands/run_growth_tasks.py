"""
One Growth Agent round, for cron (the growth-scheduler service runs the same
round every 15 minutes; use one or the other):

    python manage.py run_growth_tasks [--dry-run]

The round does quick housekeeping (onboarding, stuck sends, expired approvals),
then queues one ``brand_tick`` per active brand. Each tick prepares at most
3 drafts and sends at most 5 approved actions for its brand, checks its
outreach replies, drafts due follow-ups, and runs its daily and weekly checks
(indexing, social, guest sites, keywords, site watch, ranks, weekly summary).
Without a broker (local dev) the ticks run inline.

Every protection is unchanged: drafts wait for the owner's approval, and sends
are claimed under row locks, so overlapping rounds never prepare or send the
same thing twice.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.growth_agent.services.control.dispatch import dispatchable
from apps.growth_agent.services.execution.executor import due_tasks
from apps.growth_agent.services.scheduling import tick
from apps.growth_agent.services.scheduling.round import active_programs, run_round


class Command(BaseCommand):
    help = "Run one Growth Agent round: housekeeping, then one tick per active brand."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="List what each brand would do, without doing it."
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            self._dry_run()
            return
        result = run_round()
        if result.skipped:
            self.stdout.write("Another round is running; skipped.")
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Round done: {result.ticks} brand(s) queued, {result.recovered} stuck send(s) recovered, "
                f"{result.expired} approval(s) expired, {result.released} stale task(s) re-queued."
            )
        )

    def _dry_run(self) -> None:
        today = timezone.now().date()
        for program in active_programs():
            tasks = due_tasks(today).filter(cycle__program=program)[: tick.PREPARES_PER_TICK]
            actions = dispatchable(today).filter(task__cycle__program=program)[: tick.SENDS_PER_TICK]
            for task in tasks:
                self.stdout.write(f"program {program.id}: task {task.id} [{task.channel}] {task.title}")
            for action in actions:
                self.stdout.write(f"program {program.id}: approved action {action.public_id} [{action.kind}]")
