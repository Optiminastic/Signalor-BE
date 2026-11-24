"""
The agent reads the analyzer's technical checks: the chat's get_site_health and the
weekly report name failed checks and count passed ones.

Run: python manage.py test apps.growth_agent.tests.test_technical_checks_agent --settings=config.settings.test
"""

from apps.analyzer.models import AnalysisRun
from apps.analyzer.pipeline import technical_checks as tc
from apps.analyzer.services import technical_checks as service

from ..services import weekly_summary
from ..services.chat import insight_tools
from .test_execution_flow import ExecutionTestCase

CHECKS = [
    tc.Check("robots_ai_bots", "AI crawlers allowed in robots.txt", "failed", "robots.txt blocks GPTBot."),
    tc.Check("sitemap_found", "Sitemap found", "passed", "12 URLs listed."),
    tc.Check("sitemap_broken_urls", "No broken URLs in the sitemap", "passed", "All 12 respond directly."),
    tc.Check("sitemap_redirects", "No redirects in the sitemap", "passed", "All 12 respond directly."),
    tc.Check("sitemap_fresh", "Sitemap kept up to date", "unknown", "No <lastmod> dates."),
]


class AgentTechnicalCheckTests(ExecutionTestCase):
    def store_checks(self):
        run = AnalysisRun.objects.create(
            organization=self.pro_org,
            url=self.pro_org.url,
            email=self.pro_org.owner_email,
            status=AnalysisRun.Status.COMPLETE,
        )
        service.store(run, CHECKS)

    def test_site_health_reports_each_check_status(self):
        self.store_checks()
        checks = insight_tools.get_site_health(self.program, {})["technical_checks"]["checks"]
        self.assertEqual(
            {c["check"]: c["status"] for c in checks}["AI crawlers allowed in robots.txt"], "failed"
        )
        self.assertEqual(len(checks), 5)

    def test_site_health_without_checks_says_none(self):
        self.assertEqual(insight_tools.get_site_health(self.program, {})["technical_checks"]["checks"], [])

    def test_weekly_report_names_failures_and_counts_passes(self):
        self.store_checks()
        text = weekly_summary.build(self.program)
        self.assertIn("## Site checks", text)
        self.assertIn("**3** of 5 technical checks passed", text)
        self.assertIn("robots.txt blocks GPTBot.", text)
        self.assertNotIn("Needs fixing: sitemap found", text)

    def test_weekly_report_skips_the_section_without_checks(self):
        self.assertNotIn("## Site checks", weekly_summary.build(self.program))
