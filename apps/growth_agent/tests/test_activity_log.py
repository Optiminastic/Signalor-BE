"""
The Activity log by category, with paging; golden keywords and ranking moves.

Run: python manage.py test apps.growth_agent.tests.test_activity_log --settings=config.settings.test
"""

from django.test import SimpleTestCase

from ..models import AgentActionLog
from ..services import activity
from ..services.keywords import gaps as rules
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_execution_flow import ExecutionTestCase

ROWS = [
    ("satellite_content", "publish", "articles"),
    ("", "satellite_topics_planned", "articles"),
    ("guest_outreach", "prepared", "guest"),
    ("", "guest_sites_found", "guest"),
    ("", "keywords_refreshed", "keywords"),
    ("", "golden_keywords_found", "keywords"),
    ("", "keyword_tool_connected", "keywords"),
    ("owned_social", "publish", "social"),
    ("", "social_checked", "social"),
    ("", "site_anomaly", "site"),
    ("", "indexing_checked", "site"),
    ("outreach_email", "publish", "outreach"),
    ("", "contact_added", "outreach"),
    ("", "weekly_summary_posted", "other"),
]


class CategoryTests(SimpleTestCase):
    def test_every_kind_of_row_lands_in_its_category(self):
        for channel, action, expected in ROWS:
            self.assertEqual(activity.category_of(channel, action), expected, (channel, action))


class ActivityApiTests(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        for channel, action, _ in ROWS:
            AgentActionLog.objects.create(
                program=self.program, channel=channel, action=action, outcome=AgentActionLog.Outcome.SUCCEEDED
            )
        self.base = f"/api/growth/programs/{self.pro_org.slug}/activity/"

    def test_the_database_filter_agrees_with_category_of(self):
        for name in activity.CATEGORIES:
            body = self.signed("GET", f"{self.base}{name}/", PRO_EMAIL).json()
            expected = sorted(a for _, a, cat in ROWS if cat == name)
            self.assertEqual(sorted(e["action"] for e in body["activity"]), expected, name)
            self.assertTrue(all(e["category"] == name for e in body["activity"]))

    def test_all_returns_every_row_with_its_category(self):
        body = self.signed("GET", self.base, PRO_EMAIL).json()
        self.assertEqual(len(body["activity"]), len(ROWS))
        self.assertIsNone(body["next_before"])

    def test_paging_with_before(self):
        for _ in range(activity.PAGE_SIZE):
            AgentActionLog.objects.create(
                program=self.program, action="keywords_refreshed", outcome="succeeded"
            )
        first = self.signed("GET", self.base, PRO_EMAIL).json()
        self.assertEqual(len(first["activity"]), activity.PAGE_SIZE)
        second = self.signed("GET", f"{self.base}all/{first['next_before']}/", PRO_EMAIL).json()
        self.assertEqual(len(second["activity"]), len(ROWS))
        ids = [e["id"] for e in first["activity"] + second["activity"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_unknown_category_and_other_owners_are_refused(self):
        self.assertEqual(self.signed("GET", f"{self.base}nonsense/", PRO_EMAIL).status_code, 404)
        self.assertIn(self.signed("GET", self.base, OTHER_EMAIL).status_code, (403, 404))


def gap(keyword, volume, new=True):
    return {
        "keyword": keyword,
        "search_volume": volume,
        "new": new,
        "rivals": [{"domain": "acme.io", "position": 2}],
    }


class KeywordFindingTests(SimpleTestCase):
    def test_golden_keywords_are_new_high_demand_gaps(self):
        rows = [gap("geo audit", 2400), gap("llm seo", 900, new=False), gap("tiny", 50), gap("ai seo", 600)]
        found = rules.golden(rows, first_read=False)
        self.assertEqual([g["keyword"] for g in found], ["geo audit", "ai seo"])
        self.assertEqual(found[0]["rival"], "acme.io")

    def test_on_the_first_read_the_best_gaps_count_as_golden(self):
        rows = [gap("geo audit", 2400, new=False), gap("llm seo", 900, new=False)]
        self.assertEqual(len(rules.golden(rows, first_read=True)), 2)

    def test_rank_changes(self):
        before = [
            {"keyword": "a", "position": 12},
            {"keyword": "b", "position": 4},
            {"keyword": "c", "position": 30},
        ]
        before.append({"keyword": "d", "position": 9})
        now = [
            {"keyword": "a", "position": 6},
            {"keyword": "b", "position": 15},
            {"keyword": "c", "position": 20},
        ]
        now.append({"keyword": "d", "position": 5})
        changes = rules.rank_changes(before, now)
        self.assertEqual([r["keyword"] for r in changes["entered"]], ["a"])
        self.assertEqual([r["keyword"] for r in changes["left"]], ["b"])
        self.assertEqual({r["keyword"] for r in changes["up"]}, {"c", "d"})
        self.assertEqual(changes["down"], [])
