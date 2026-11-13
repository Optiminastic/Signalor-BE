"""
The chat agent can see indexing, Bing and satellite articles (get_indexing),
so a "what's built?" answer covers them too.

Run: python manage.py test apps.growth_agent.tests.test_chat_indexing_tool --settings=config.settings.test
"""

from django.utils import timezone

from ..services.chat import read_tools, tools
from .test_execution_flow import ExecutionTestCase


class IndexingToolTests(ExecutionTestCase):
    def test_get_indexing_reports_google_bing_and_satellite_articles(self):
        self.make_task(
            channel="satellite_content",
            tactic="satellite_article",
            target="ahrefs vs semrush",
            target_url="https://betterversus.com",
            scheduled_for=timezone.now().date(),
        )
        result = read_tools.get_indexing(self.program, {})
        self.assertEqual(result["google"]["search_console"], "not_connected")
        self.assertEqual(result["bing_weekly_sending"]["enabled"], False)
        topic = result["satellite_articles"]["planned_topics"][0]
        self.assertEqual((topic["topic"], topic["site"]), ("ahrefs vs semrush", "https://betterversus.com"))
        self.assertEqual(result["satellite_articles"]["published"], [])

    def test_the_plan_names_satellite_articles_and_their_site(self):
        self.make_task(
            channel="satellite_content", tactic="satellite_article", target_url="https://thepickpost.com"
        )
        tasks = read_tools.get_plan(self.program, {})["tasks"]
        satellite = next(t for t in tasks if t["tactic"] == "satellite_article")
        self.assertEqual(satellite["site"], "https://thepickpost.com")

    def test_the_tool_is_registered_as_read_only(self):
        self.assertIn("get_indexing", [spec["function"]["name"] for spec in tools.specs()])
        self.assertNotIn("get_indexing", tools.WRITE_TOOLS)
