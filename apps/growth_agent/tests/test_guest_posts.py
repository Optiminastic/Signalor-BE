"""
Guest posting step 1: find sites that accept guest articles on the brand's
topic, check each one, and add them to the plan. Search and page fetches are faked.

Run: python manage.py test apps.growth_agent.tests.test_guest_posts --settings=config.settings.test
"""

from unittest import mock

from django.test import SimpleTestCase, override_settings

from ..models import AgentActionLog, GrowthTask, OutreachContact, PositioningReport
from ..services.execution.executor import due_tasks
from ..services.guest_posts import candidates as rules
from ..services.guest_posts import finder
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_execution_flow import ExecutionTestCase

REPORT = {
    "citation_gaps": [{"domain": "cited-blog.com", "prompts": 5}],
    "lost_prompts": [
        {"prompt": "best geo tool", "winning_rivals": [{"name": "Acme", "engines": ["chatgpt"]}]}
    ],
    "context_gaps": {"missing_topics": ["AI citations", "a very long topic that is far too wordy to search"]},
}
INVITE = (
    "<html><body><h1>Write for us</h1><p>We publish guest posts about AI citations and GEO.</p></body></html>"
)
FARM = "<p>Write for us. Guest post price: $150 per post. We accept all niches.</p>"
OFF_TOPIC = "<p>Write for us! Guest posts about gardening and roses.</p>"


class CandidateRuleTests(SimpleTestCase):
    def test_topics_are_short_and_unique(self):
        self.assertEqual(rules.topics(REPORT), ["AI citations", "best geo tool"])

    def test_a_real_invitation_on_topic_passes(self):
        self.assertTrue(rules.check_page(INVITE, ["AI citations"]).ok)

    def test_link_sellers_and_off_topic_sites_fail(self):
        self.assertTrue(rules.check_page(FARM, ["AI citations"]).sells_links)
        self.assertFalse(rules.check_page(OFF_TOPIC, ["AI citations"]).ok)

    def test_brand_satellites_contacted_and_competitor_sites_are_excluded(self):
        blocked = {"brand.com"}
        self.assertTrue(rules.excluded("brand.com", blocked_domains=blocked, rivals=set()))
        self.assertTrue(rules.excluded("acme.io", blocked_domains=set(), rivals={"acme"}))
        self.assertFalse(rules.excluded("geo-weekly.com", blocked_domains=blocked, rivals={"acme"}))


def fake_fetch(pages):
    return lambda session, url: pages.get(url)


@override_settings(SATELLITE_SITES={"research": "https://brightsfindings.com"})
class GuestFinderTests(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        report = PositioningReport.objects.create(
            program=self.program, status=PositioningReport.Status.COMPLETE, **REPORT
        )
        self.cycle.positioning_report = report
        self.cycle.save(update_fields=["positioning_report"])
        self.base = f"/api/growth/programs/{self.pro_org.slug}/guest-posts/"
        self._body = {}

    def find(self, pages, results):
        with (
            mock.patch.object(finder, "fetch", side_effect=fake_fetch(pages)),
            mock.patch.object(finder, "search", side_effect=lambda q: results.get(q, [])),
        ):
            return finder.run_find(self.program)

    def test_cited_sites_come_first_and_checked_sites_join_the_plan(self):
        query = rules.queries(["AI citations"])[0]
        results = {
            query: ["https://geo-weekly.com/write-for-us", "https://spam.net/guest", "https://acme.io/w"]
        }
        pages = {
            "https://cited-blog.com/write-for-us": INVITE,
            "https://geo-weekly.com/write-for-us": INVITE,
            "https://spam.net/guest": FARM,
            "https://acme.io/w": INVITE,
        }
        self.assertEqual(self.find(pages, results), 2)
        sites = list(GrowthTask.objects.filter(tactic="guest_post").order_by("scheduled_for"))
        self.assertEqual([s.target for s in sites], ["cited-blog.com", "geo-weekly.com"])
        self.assertEqual(sites[0].draft_payload["guest"]["source"], "cited")
        self.assertEqual(sites[0].channel, "guest_outreach")
        self.assertTrue(AgentActionLog.objects.filter(action=finder.FOUND).exists())

    def test_already_contacted_sites_are_skipped(self):
        OutreachContact.objects.create(
            organization=self.pro_org, email="ed@cited-blog.com", domain="cited-blog.com"
        )
        self.assertEqual(self.find({"https://cited-blog.com/write-for-us": INVITE}, {}), 0)

    def test_never_more_than_the_monthly_cap(self):
        for i in range(finder.MONTHLY_CAP):
            self.make_task(channel="guest_outreach", tactic="guest_post", target=f"s{i}.com")
        self.assertEqual(self.find({"https://cited-blog.com/write-for-us": INVITE}, {}), 0)

    def test_guest_sites_are_shown_but_never_executed_until_pitching_exists(self):
        self.find({"https://cited-blog.com/write-for-us": INVITE}, {})
        self.assertFalse(due_tasks(self.cycle.starts_on).filter(tactic="guest_post").exists())

    def test_the_tab_lists_sites_and_only_the_owner_sees_them(self):
        self.find({"https://cited-blog.com/write-for-us": INVITE}, {})
        body = self.signed("GET", self.base, PRO_EMAIL).json()
        self.assertEqual(body["sites"][0]["domain"], "cited-blog.com")
        self.assertEqual(body["sites"][0]["stage"], "to_pitch")
        self.assertIn(self.signed("GET", self.base, OTHER_EMAIL).status_code, (403, 404))

    def test_find_now_runs_at_most_every_six_hours(self):
        with mock.patch("apps.growth_agent.analysis_tasks.dispatch_guest_site_find"):
            first = self.signed("POST", f"{self.base}find/", PRO_EMAIL)
            second = self.signed("POST", f"{self.base}find/", PRO_EMAIL)
        self.assertEqual(first.status_code, 202)
        self.assertTrue(first.json()["searching"])
        self.assertEqual(second.status_code, 409)
