"""
Read-only inventory of existing Growth programs (Phase 1, step 1).

The inventory is what the cutover decision is made from, so it must be accurate
and must never print an owner's email address.

Run: python manage.py test apps.growth_agent.tests.test_inventory_modes --settings=config.settings.test
"""

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.utils import timezone

from ..models import (
    ChannelMode,
    GrowthCycle,
    GrowthProgram,
    GrowthTask,
    OutreachContact,
    OutreachThread,
)
from ..services import inventory
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_execution_flow import ExecutionTestCase


class InventoryTestCase(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        self.today = timezone.now().date()

    def make_thread(self, task: GrowthTask, *, status: str, follow_up_in_days: int | None) -> OutreachThread:
        contact = OutreachContact.objects.create(
            organization=self.pro_org,
            email=f"editor{OutreachContact.objects.count()}@publisher.example",
            domain="publisher.example",
        )
        due = None if follow_up_in_days is None else self.today + timedelta(days=follow_up_in_days)
        return OutreachThread.objects.create(
            task=task,
            contact=contact,
            gmail_thread_id="thread-1",
            subject="Hello",
            status=status,
            next_follow_up_on=due,
        )

    def entry_for(self, organization) -> inventory.ProgramInventory:
        return next(e for e in inventory.collect(self.today) if e.org_slug == organization.slug)


class CollectTests(InventoryTestCase):
    def test_reports_status_modes_and_kill_switch_as_stored(self):
        entry = self.entry_for(self.pro_org)
        self.assertEqual(entry.status, GrowthProgram.Status.ACTIVE)
        self.assertEqual(entry.channel_modes, self.program.channel_modes)
        self.assertFalse(entry.kill_switch)

    def test_lists_the_channels_still_set_to_autonomous(self):
        self.program.channel_modes = {
            "owned_content": ChannelMode.AUTONOMOUS.value,
            "outreach_email": ChannelMode.APPROVAL.value,
            "community": ChannelMode.DRY_RUN.value,
        }
        self.program.save(update_fields=["channel_modes"])
        self.assertEqual(self.entry_for(self.pro_org).autonomous_channels, ["owned_content"])

    def test_counts_tasks_of_the_active_cycle_by_status(self):
        self.make_task(status=GrowthTask.Status.DONE)
        self.make_task(status=GrowthTask.Status.DONE)
        self.make_task(status=GrowthTask.Status.AWAITING_APPROVAL)
        entry = self.entry_for(self.pro_org)
        self.assertEqual(entry.active_cycle_id, self.cycle.id)
        self.assertEqual(
            entry.tasks_by_status,
            {
                GrowthTask.Status.PLANNED: 1,
                GrowthTask.Status.DONE: 2,
                GrowthTask.Status.AWAITING_APPROVAL: 1,
            },
        )

    def test_tasks_of_an_old_cycle_are_not_counted(self):
        old = GrowthCycle.objects.create(
            program=self.program,
            status=GrowthCycle.Status.COMPLETED,
            starts_on=self.today - timedelta(days=60),
            ends_on=self.today - timedelta(days=33),
        )
        self.make_task(cycle=old, status=GrowthTask.Status.DONE)
        self.assertEqual(self.entry_for(self.pro_org).tasks_by_status, {GrowthTask.Status.PLANNED: 1})

    def test_counts_tasks_stuck_in_executing(self):
        self.make_task(status=GrowthTask.Status.EXECUTING)
        self.assertEqual(self.entry_for(self.pro_org).executing_tasks, 1)

    def test_counts_open_threads_and_those_with_a_follow_up_due(self):
        self.make_thread(self.task, status=OutreachThread.Status.SENT, follow_up_in_days=0)
        self.make_thread(self.task, status=OutreachThread.Status.FOLLOWED_UP, follow_up_in_days=-2)
        self.make_thread(self.task, status=OutreachThread.Status.SENT, follow_up_in_days=3)
        self.make_thread(self.task, status=OutreachThread.Status.REPLIED, follow_up_in_days=None)
        self.make_thread(self.task, status=OutreachThread.Status.CLOSED, follow_up_in_days=-1)
        entry = self.entry_for(self.pro_org)
        self.assertEqual(entry.open_threads, 3)
        self.assertEqual(entry.threads_follow_up_due, 2)

    def test_program_without_an_active_cycle(self):
        self.cycle.status = GrowthCycle.Status.CANCELLED
        self.cycle.save(update_fields=["status"])
        entry = self.entry_for(self.pro_org)
        self.assertIsNone(entry.active_cycle_id)
        self.assertEqual(entry.tasks_by_status, {})

    def test_every_program_is_listed_once(self):
        GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)
        slugs = [e.org_slug for e in inventory.collect(self.today)]
        self.assertCountEqual(slugs, [self.pro_org.slug, self.other_org.slug])

    def test_query_count_does_not_grow_with_the_number_of_programs(self):
        with self.assertNumQueries(inventory.QUERY_COUNT):
            inventory.collect(self.today)
        GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)
        with self.assertNumQueries(inventory.QUERY_COUNT):
            inventory.collect(self.today)


class ReadOnlyTests(InventoryTestCase):
    def test_collecting_changes_nothing(self):
        self.make_task(status=GrowthTask.Status.EXECUTING)
        before = (
            list(GrowthProgram.objects.values()),
            list(GrowthCycle.objects.values()),
            list(GrowthTask.objects.values()),
        )
        inventory.collect(self.today)
        call_command("growth_inventory_modes", stdout=StringIO())
        after = (
            list(GrowthProgram.objects.values()),
            list(GrowthCycle.objects.values()),
            list(GrowthTask.objects.values()),
        )
        self.assertEqual(before, after)


class CommandTests(InventoryTestCase):
    def run_command(self, *args: str) -> str:
        out = StringIO()
        call_command("growth_inventory_modes", *args, stdout=out)
        return out.getvalue()

    def test_prints_one_block_per_program_and_a_summary(self):
        GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)
        output = self.run_command()
        self.assertIn(self.pro_org.slug, output)
        self.assertIn(self.other_org.slug, output)
        self.assertIn("Programs: 2", output)

    def test_summary_counts_programs_with_an_autonomous_channel(self):
        output = self.run_command()
        self.assertIn("With at least one autonomous channel: 1", output)

    def test_never_prints_an_email_address(self):
        self.make_thread(self.task, status=OutreachThread.Status.SENT, follow_up_in_days=0)
        GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)
        output = self.run_command()
        for secret in (PRO_EMAIL, OTHER_EMAIL, "publisher.example", "@"):
            self.assertNotIn(secret, output)

    def test_no_programs(self):
        GrowthProgram.objects.all().delete()
        self.assertIn("Programs: 0", self.run_command())
