"""
The weekly report behind the Agent page's icon, and its email.

Run: python manage.py test apps.growth_agent.tests.test_weekly_reports --settings=config.settings.test
"""

from datetime import timedelta

from django.utils import timezone

from ..models import GrowthProgram, WeeklyReport
from ..services import weekly_email, weekly_summary
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_execution_flow import ExecutionTestCase


class WeeklyReportApiTests(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        GrowthProgram.objects.filter(pk=self.program.pk).update(created_at=timezone.now() - timedelta(days=8))
        self.program.refresh_from_db()
        self.url = f"/api/growth/programs/{self.pro_org.slug}/weekly-reports/"

    def test_before_the_first_report_the_icon_shows_this_week_so_far(self):
        body = self.signed("GET", self.url, PRO_EMAIL).json()
        self.assertEqual(body["reports"], [])
        self.assertIn("## Coming up", body["current"]["content"])
        self.assertEqual(body["next_report_on"], timezone.now().date().isoformat())

    def test_saved_reports_are_listed_latest_first(self):
        WeeklyReport.objects.create(
            program=self.program, week_of=timezone.now().date() - timedelta(days=14), content="old"
        )
        weekly_summary.post(self.program)
        reports = self.signed("GET", self.url, PRO_EMAIL).json()["reports"]
        self.assertEqual(len(reports), 2)
        self.assertGreater(reports[0]["week_of"], reports[1]["week_of"])

    def test_another_brands_owner_cannot_read_the_reports(self):
        self.assertIn(self.signed("GET", self.url, OTHER_EMAIL).status_code, (403, 404))


class WeeklyEmailTests(ExecutionTestCase):
    def report(self, content):
        return WeeklyReport.objects.create(
            program=self.program, week_of=timezone.now().date(), content=content
        )

    def test_html_is_escaped_and_formatted(self):
        html = weekly_email.html("## This week\n- **3** sent <script>x</script>", "https://signalor.ai/r")
        self.assertIn("<h3", html)
        self.assertIn("<strong>3</strong>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_plain_text_drops_the_formatting_symbols(self):
        self.assertEqual(weekly_email.plain_text("## Social\n- **2** posts"), "SOCIAL\n- 2 posts")

    def test_the_email_links_to_that_weeks_report(self):
        report = self.report("## This week\n- **1** sent")
        url = weekly_email.report_url(report)
        self.assertIn(f"/dashboard/{self.pro_org.slug}/agent-mode?report={report.week_of.isoformat()}", url)
