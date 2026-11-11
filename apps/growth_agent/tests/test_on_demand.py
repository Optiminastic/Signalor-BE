"""
On-demand actions: preview (dry run, schedule untouched, not capped) and run now.

Run: python manage.py test apps.growth_agent.tests.test_on_demand --settings=config.settings.test
"""

from datetime import timedelta
from unittest import mock

from django.utils import timezone

from ..models import AgentActionLog, GrowthAction, GrowthTask
from ..services.execution import guardrails, on_demand
from .test_api import PRO_EMAIL
from .test_execution_flow import REGISTRY, ExecutionTestCase, FakeChannel

PREVIEW_DISPATCH = "apps.growth_agent.views.dispatch_preview"
RUN_DISPATCH = "apps.growth_agent.views.dispatch_task_run"


class PreviewTests(ExecutionTestCase):
    def test_preview_runs_dry_and_keeps_task_scheduled(self):
        channel = FakeChannel()
        on_demand.request_preview(self.task)
        with mock.patch.dict(REGISTRY, {"owned_content": channel}):
            on_demand.run_preview(self.task.id)
        self.task.refresh_from_db()
        self.assertEqual((channel.prepares, channel.executed), (1, []))
        self.assertFalse(GrowthAction.objects.exists())
        self.assertEqual(self.task.status, GrowthTask.Status.PLANNED)
        self.assertEqual(self.task.attempts, 0)
        self.assertEqual(self.task.result_payload["preview"], {"status": "complete", "title": "Draft page"})

    def test_preview_does_not_use_the_daily_cap(self):
        with mock.patch.dict(REGISTRY, {"owned_content": FakeChannel()}):
            for _ in range(guardrails.DAILY_CHANNEL_CAPS["owned_content"] + 1):
                on_demand.run_preview(self.task.id)
        self.assertTrue(guardrails.check(self.task).allowed)
        self.assertFalse(AgentActionLog.objects.filter(action="publish").exists())

    def test_crash_ends_preview_as_failed(self):
        on_demand.request_preview(self.task)
        with mock.patch.dict(REGISTRY, {"owned_content": FakeChannel(crash=True)}):
            on_demand.run_preview(self.task.id)
        self.task.refresh_from_db()
        self.assertEqual(self.task.result_payload["preview"]["status"], "failed")

    def test_cannot_preview_twice_at_once_or_a_finished_task(self):
        on_demand.request_preview(self.task)
        with self.assertRaises(on_demand.OnDemandError):
            on_demand.request_preview(self.task)
        GrowthTask.objects.filter(pk=self.task.pk).update(status=GrowthTask.Status.DONE)
        self.task.refresh_from_db()
        with self.assertRaises(on_demand.OnDemandError):
            on_demand.request_preview(self.task)


class RunNowTests(ExecutionTestCase):
    def test_pulls_future_task_to_today(self):
        GrowthTask.objects.filter(pk=self.task.pk).update(
            scheduled_for=timezone.now().date() + timedelta(days=5)
        )
        self.task.refresh_from_db()
        on_demand.schedule_now(self.task)
        self.assertEqual(self.task.scheduled_for, timezone.now().date())

    def test_unregistered_channel_or_paused_program_refused(self):
        entity = self.make_task(channel="entity", tactic="entity_schema")
        with self.assertRaises(on_demand.OnDemandError):
            on_demand.schedule_now(entity)
        self.program.status = "paused"
        self.program.save()
        self.task.refresh_from_db()
        with self.assertRaises(on_demand.OnDemandError):
            on_demand.schedule_now(self.task)


class TaskActionApiTests(ExecutionTestCase):
    @mock.patch(PREVIEW_DISPATCH)
    def test_preview_endpoint(self, dispatch):
        self._body = {}
        path = f"/api/growth/programs/{self.pro_org.slug}/tasks/{self.task.id}/preview/"
        self.assertEqual(self.signed("POST", path, PRO_EMAIL).status_code, 202)
        dispatch.assert_called_once_with(self.task.id)
        self.assertEqual(self.signed("POST", path, PRO_EMAIL).status_code, 409)  # already running

    @mock.patch(RUN_DISPATCH)
    def test_cannot_act_on_another_brands_task(self, dispatch):
        self._body = {}
        path = f"/api/growth/programs/{self.other_org.slug}/tasks/{self.task.id}/run/"
        self.assertEqual(self.signed("POST", path, PRO_EMAIL).status_code, 404)
        dispatch.assert_not_called()
