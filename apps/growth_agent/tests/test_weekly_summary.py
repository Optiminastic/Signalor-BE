"""
The weekly summary the agent posts in chat: exact numbers from what it did,
once a week per brand, from the first full week.

Run: python manage.py test apps.growth_agent.tests.test_weekly_summary --settings=config.settings.test
"""

from datetime import timedelta
from unittest import mock

from django.core import mail
from django.utils import timezone

from ..models import AgentActionLog, AgentConversation, GrowthProgram, GrowthTask, WeeklyReport
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

    def test_it_is_saved_as_a_weekly_report_and_emailed_once_a_week_not_a_chat(self):
        with self.captureOnCommitCallbacks(execute=True):
            report = weekly_summary.post(self.program)
        self.assertIsNotNone(report)
        self.assertIn("## Coming up", report.content)
        self.assertFalse(AgentConversation.objects.exists())  # no new chat any more
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.program.organization.owner_email])
        report.refresh_from_db()
        self.assertIsNotNone(report.emailed_at)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertIsNone(weekly_summary.post(self.program))
        self.assertEqual(WeeklyReport.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_mail_failure_keeps_the_report(self):
        with mock.patch(
            "django.core.mail.EmailMultiAlternatives.send", side_effect=RuntimeError("smtp down")
        ):
            with self.captureOnCommitCallbacks(execute=True):
                report = weekly_summary.post(self.program)
        report.refresh_from_db()
        self.assertIsNone(report.emailed_at)
        self.assertTrue(WeeklyReport.objects.filter(pk=report.pk).exists())

    def test_the_next_report_is_due_a_week_after_the_last(self):
        self.assertEqual(weekly_summary.next_report_on(self.program), timezone.now().date())
        weekly_summary.post(self.program)
        self.assertEqual(
            weekly_summary.next_report_on(self.program), (timezone.now() + timedelta(days=7)).date()
        )

    def test_a_new_brand_gets_no_summary_in_its_first_week(self):
        GrowthProgram.objects.filter(pk=self.program.pk).update(created_at=timezone.now() - timedelta(days=2))
        self.program.refresh_from_db()
        self.assertIsNone(weekly_summary.post(self.program))

    def test_a_paused_brand_gets_no_summary(self):
        self.program.status = GrowthProgram.Status.PAUSED
        self.program.save()
        self.assertIsNone(weekly_summary.post(self.program))
