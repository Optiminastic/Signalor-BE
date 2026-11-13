"""
The weekly summary the agent posts in chat: exact numbers from what it did,
once a week per brand, from the first full week.

Run: python manage.py test apps.growth_agent.tests.test_weekly_summary --settings=config.settings.test
"""

from datetime import timedelta

from django.utils import timezone

from ..models import AgentActionLog, AgentConversation, AgentMessage, GrowthProgram, GrowthTask
from ..services import weekly_summary
from ..services.execution import audit
from .test_execution_flow import ExecutionTestCase


class WeeklySummaryTests(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        GrowthProgram.objects.filter(pk=self.program.pk).update(created_at=timezone.now() - timedelta(days=8))
        self.program.refresh_from_db()

    def test_the_summary_counts_what_was_published_and_whats_coming(self):
        done = self.make_task(title="Pitch best-tools.com", status=GrowthTask.Status.DONE)
        audit.record(self.program, done, "publish", AgentActionLog.Outcome.SUCCEEDED, {"url": "https://x"})
        self.make_task(
            title="Write a comparison page", scheduled_for=timezone.now().date() + timedelta(days=2)
        )
        text = weekly_summary.build(self.program)
        self.assertIn("**1** sent or published this week", text)
        self.assertIn("Pitch best-tools.com", text)
        self.assertIn("**0** drafts waiting for your approval", text)
        self.assertIn("Search Console isn't connected yet", text)
        self.assertIn("## Coming up", text)

    def test_it_is_posted_as_its_own_conversation_once_a_week(self):
        conversation = weekly_summary.post(self.program)
        self.assertIsNotNone(conversation)
        self.assertTrue(conversation.title.startswith("Weekly summary"))
        message = AgentMessage.objects.get(conversation=conversation)
        self.assertEqual((message.role, message.status), ("assistant", "complete"))
        self.assertIsNone(weekly_summary.post(self.program))
        self.assertEqual(AgentConversation.objects.count(), 1)

    def test_a_new_brand_gets_no_summary_in_its_first_week(self):
        GrowthProgram.objects.filter(pk=self.program.pk).update(created_at=timezone.now() - timedelta(days=2))
        self.program.refresh_from_db()
        self.assertIsNone(weekly_summary.post(self.program))

    def test_a_paused_brand_gets_no_summary(self):
        self.program.status = GrowthProgram.Status.PAUSED
        self.program.save()
        self.assertIsNone(weekly_summary.post(self.program))
