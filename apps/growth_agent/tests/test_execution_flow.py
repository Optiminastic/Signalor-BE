"""
Preparation flow: guardrails, executor lifecycle, plan rebuild, activity API.

The executor only prepares drafts. Approving and sending them is covered in
test_approval_flow.

Run: python manage.py test apps.growth_agent.tests.test_execution_flow --settings=config.settings.test
"""

from datetime import timedelta
from unittest import mock

from django.utils import timezone

from ..models import (
    AgentActionLog,
    ChannelMode,
    GrowthAction,
    GrowthCycle,
    GrowthProgram,
    GrowthTask,
    PositioningReport,
)
from ..services.execution import executor, guardrails
from ..services.execution.article import ArticleDraft
from ..services.execution.channel_base import ChannelOutcome, Prepared
from ..services.planning import plan as planning
from .test_api import OTHER_EMAIL, PRO_EMAIL, GrowthApiTestCase

REGISTRY = "apps.growth_agent.services.execution.executor.REGISTRY"


def fake_prepared() -> Prepared:
    """A valid article draft, so the review endpoints can render it."""
    article = ArticleDraft(title="Draft page", meta_description="About the draft.", answer="A short answer.")
    return Prepared(
        kind=GrowthAction.Kind.PUBLISH_ARTICLE,
        connector="wordpress",
        payload={"article": article.model_dump(), "slug": "draft-page"},
        destination={"platform": "wordpress"},
        preview={"title": "Draft page"},
    )


class FakeChannel:
    """Stands in for a real channel and counts how often each step ran.

    ``failure`` is what ``prepare`` returns instead of a draft. ``sent`` is what
    ``execute`` returns. Nothing here touches the network.
    """

    def __init__(self, failure=None, ready="", crash=False, sent=None, send_crash=False):
        self.failure = failure
        self.ready = ready
        self.crash = crash
        self.sent = sent or ChannelOutcome(True, evidence_url="https://pro.example/blog/x", payload={"t": 1})
        self.send_crash = send_crash
        self.prepares = 0
        self.executed = []

    def readiness(self, program):
        return self.ready

    def prepare(self, task):
        self.prepares += 1
        if self.crash:
            raise RuntimeError("boom")
        return self.failure or fake_prepared()

    def execute(self, task, version):
        self.executed.append(version)
        if self.send_crash:
            raise RuntimeError("boom")
        return self.sent


class ExecutionTestCase(GrowthApiTestCase):
    def setUp(self):
        super().setUp()
        self.program = GrowthProgram.objects.create(
            organization=self.pro_org, created_by_email=PRO_EMAIL, status=GrowthProgram.Status.ACTIVE
        )
        today = timezone.now().date()
        self.cycle = GrowthCycle.objects.create(
            program=self.program, starts_on=today, ends_on=today + timedelta(days=27)
        )
        self.task = self.make_task()

    def make_task(self, **overrides):
        fields = {
            "cycle": self.cycle,
            "week": 1,
            "scheduled_for": timezone.now().date(),
            "channel": "owned_content",
            "tactic": "answer_content",
            "title": "Publish a page",
            "target": "AI citations",
        }
        fields.update(overrides)
        return GrowthTask.objects.create(**fields)

    def run_with(self, channel):
        with mock.patch.dict(REGISTRY, {"owned_content": channel}):
            executor.run_task(self.task.id)
        self.task.refresh_from_db()


class GuardrailTests(ExecutionTestCase):
    def test_kill_switch_blocks(self):
        GrowthProgram.objects.filter(pk=self.program.pk).update(kill_switch=True)
        self.task.refresh_from_db()
        self.assertFalse(guardrails.check(self.task).allowed)

    def test_non_autonomous_tactic_from_an_old_plan_is_blocked(self):
        task = self.make_task(channel="entity", tactic="wikidata_entry")
        decision = guardrails.check(task)
        self.assertFalse(decision.allowed)
        self.assertFalse(decision.retry_later)

    def test_daily_cap_is_temporary(self):
        cap = guardrails.DAILY_CHANNEL_CAPS["owned_content"]
        for _ in range(cap):
            AgentActionLog.objects.create(
                program=self.program,
                task=self.task,
                channel="owned_content",
                action="publish",
                outcome=AgentActionLog.Outcome.SUCCEEDED,
            )
        decision = guardrails.check(self.task)
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.retry_later)


class ExecutorTests(ExecutionTestCase):
    def test_due_task_becomes_a_draft_awaiting_approval_and_nothing_is_sent(self):
        channel = FakeChannel()
        self.run_with(channel)
        self.assertEqual(self.task.status, GrowthTask.Status.AWAITING_APPROVAL)
        self.assertEqual(channel.executed, [])
        action = GrowthAction.objects.get(task=self.task)
        self.assertEqual(action.state, GrowthAction.State.AWAITING_APPROVAL)
        self.assertEqual(action.organization, self.pro_org)
        log = AgentActionLog.objects.get(task=self.task)
        self.assertEqual((log.action, log.outcome), ("prepared", AgentActionLog.Outcome.SUCCEEDED))

    def test_an_old_autonomous_setting_no_longer_skips_approval(self):
        self.assertEqual(self.program.channel_modes["owned_content"], ChannelMode.AUTONOMOUS.value)
        channel = FakeChannel()
        self.run_with(channel)
        self.assertEqual(self.task.status, GrowthTask.Status.AWAITING_APPROVAL)
        self.assertEqual(channel.executed, [])

    def test_dry_run_channel_drafts_without_creating_an_action(self):
        self.program.channel_modes = {
            **self.program.channel_modes,
            "owned_content": ChannelMode.DRY_RUN.value,
        }
        self.program.save(update_fields=["channel_modes"])
        channel = FakeChannel()
        self.run_with(channel)
        self.assertEqual(self.task.status, GrowthTask.Status.READY)
        self.assertEqual(self.task.result_payload, {"title": "Draft page"})
        self.assertFalse(GrowthAction.objects.exists())
        self.assertEqual(channel.executed, [])

    def test_not_connected_waits_without_using_an_attempt(self):
        self.run_with(FakeChannel(ready="Connect WordPress or the Signalor GitHub App to publish content."))
        self.assertEqual(self.task.status, GrowthTask.Status.PLANNED)
        self.assertEqual(self.task.attempts, 0)
        self.assertIn("Connect", self.task.result_payload["waiting_for"])

    def test_failure_retries_tomorrow_then_fails_after_max_attempts(self):
        channel = FakeChannel(failure=ChannelOutcome(False, "Blocked by brand-safety check."))
        self.run_with(channel)
        self.assertEqual(self.task.status, GrowthTask.Status.PLANNED)
        self.assertEqual(self.task.scheduled_for, timezone.now().date() + timedelta(days=1))
        for _ in range(executor.MAX_ATTEMPTS - 1):
            GrowthTask.objects.filter(pk=self.task.pk).update(status=GrowthTask.Status.PLANNED)
            self.run_with(channel)
        self.assertEqual(self.task.status, GrowthTask.Status.FAILED)
        self.assertEqual(self.task.attempts, executor.MAX_ATTEMPTS)

    def test_channel_crash_is_contained(self):
        self.run_with(FakeChannel(crash=True))
        self.assertEqual(self.task.status, GrowthTask.Status.PLANNED)
        self.assertEqual(self.task.result_payload["error"], "boom")

    def test_task_already_claimed_is_not_run_twice(self):
        GrowthTask.objects.filter(pk=self.task.pk).update(status=GrowthTask.Status.EXECUTING)
        channel = FakeChannel()
        self.run_with(channel)
        self.assertEqual(channel.prepares, 0)

    def test_due_tasks_only_include_registered_channels_and_due_dates(self):
        self.make_task(channel="entity", tactic="entity_schema")  # no entity executor yet
        self.make_task(scheduled_for=timezone.now().date() + timedelta(days=3))
        due = list(executor.due_tasks(timezone.now().date()))
        self.assertEqual([t.id for t in due], [self.task.id])

    def test_stale_executing_tasks_are_released(self):
        GrowthTask.objects.filter(pk=self.task.pk).update(status=GrowthTask.Status.EXECUTING)
        later = timezone.now() + executor.STALE_EXECUTION + timedelta(minutes=1)
        self.assertEqual(executor.release_stale_executions(now=later), 1)

    def test_a_task_with_an_action_is_never_released_for_re_preparation(self):
        self.run_with(FakeChannel())
        GrowthTask.objects.filter(pk=self.task.pk).update(status=GrowthTask.Status.EXECUTING)
        later = timezone.now() + executor.STALE_EXECUTION + timedelta(minutes=1)
        self.assertEqual(executor.release_stale_executions(now=later), 0)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, GrowthTask.Status.EXECUTING)


class PlanRebuildTests(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        PositioningReport.objects.create(
            program=self.program,
            status=PositioningReport.Status.COMPLETE,
            context_gaps={"missing_topics": ["AI citations"]},
        )

    def test_rebuild_replaces_untouched_plan(self):
        new_cycle, created = planning.create_plan(self.program, rebuild=True)
        self.assertTrue(created)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, GrowthCycle.Status.CANCELLED)
        self.assertNotEqual(new_cycle.pk, self.cycle.pk)

    def test_rebuild_refused_once_work_started(self):
        GrowthTask.objects.filter(pk=self.task.pk).update(status=GrowthTask.Status.DONE)
        with self.assertRaises(planning.PlanError):
            planning.create_plan(self.program, rebuild=True)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, GrowthCycle.Status.ACTIVE)


class ActivityApiTests(ExecutionTestCase):
    def test_lists_own_activity_and_platform(self):
        AgentActionLog.objects.create(
            program=self.program,
            task=self.task,
            channel="owned_content",
            action="publish",
            outcome=AgentActionLog.Outcome.SUCCEEDED,
        )
        response = self.signed("GET", f"/api/growth/programs/{self.pro_org.slug}/activity/", PRO_EMAIL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["activity"][0]["task_title"], "Publish a page")
        program = self.signed("GET", f"/api/growth/programs/{self.pro_org.slug}/", PRO_EMAIL)
        self.assertIsNone(program.data["publishing_platform"])  # nothing connected in tests

    def test_cannot_read_someone_elses_activity(self):
        GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)
        response = self.signed("GET", f"/api/growth/programs/{self.other_org.slug}/activity/", PRO_EMAIL)
        self.assertEqual(response.status_code, 404)
