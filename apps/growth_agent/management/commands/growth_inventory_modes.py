"""
List every Growth program and what is in flight for it. Read-only.

    python manage.py growth_inventory_modes

Run before the Phase 1 cutover. It shows which programs still carry the old
autonomous setting, and what the cutover has to handle for each: tasks stuck in
"executing" and outreach threads with a follow-up due. Brands are identified by
their slug. No email address is printed.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.growth_agent.services import inventory


class Command(BaseCommand):
    help = "Read-only inventory of Growth programs, their modes and in-flight work."

    def handle(self, *args, **options):
        entries = inventory.collect(timezone.now().date())
        for entry in entries:
            for line in self._block(entry):
                self.stdout.write(line)
            self.stdout.write("")
        self.stdout.write(f"Programs: {len(entries)}")
        with_autonomy = sum(1 for entry in entries if entry.autonomous_channels)
        self.stdout.write(f"With at least one autonomous channel: {with_autonomy}")
        self.stdout.write(f"With tasks stuck in executing: {sum(1 for e in entries if e.executing_tasks)}")
        self.stdout.write(f"With a follow-up due: {sum(1 for e in entries if e.threads_follow_up_due)}")

    def _block(self, entry: inventory.ProgramInventory) -> list[str]:
        cycle = (
            f"{entry.active_cycle_id} (ends {entry.active_cycle_ends_on})"
            if entry.active_cycle_id is not None
            else "none"
        )
        tasks = ", ".join(f"{status}={count}" for status, count in sorted(entry.tasks_by_status.items()))
        return [
            f"brand {entry.org_slug}",
            f"  status: {entry.status}   kill switch: {'on' if entry.kill_switch else 'off'}",
            f"  autonomous channels: {', '.join(entry.autonomous_channels) or 'none'}",
            f"  active cycle: {cycle}",
            f"  tasks: {tasks or 'none'}",
            f"  executing now: {entry.executing_tasks}",
            f"  open threads: {entry.open_threads}   follow-up due: {entry.threads_follow_up_due}",
        ]
