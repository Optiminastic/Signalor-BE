"""
Chat tools for competitor tracking, keywords and site health (stored data only).

Run: python manage.py test apps.growth_agent.tests.test_chat_insight_tools --settings=config.settings.test
"""

import json
from unittest import mock

from django.utils import timezone

from ..models import AgentActionLog, KeywordSnapshot, RankObservation
from ..services.chat import tools
from ..services.rank_tracking import rules
from ..services.site_watch import watch
from .test_rank_tracking import RankBase


class InsightToolTests(RankBase):
    def call(self, name):
        return json.loads(tools.dispatch(self.program, name, {}))

    def test_competitor_tracking_compares_players_with_plain_names(self):
        week = rules.week_of(timezone.localdate())
        for domain, position in (("pro.example", 12), ("acme.io", 3)):
            RankObservation.objects.create(
                program=self.program,
                week=week,
                kind="seo",
                keyword="ai visibility",
                domain=domain,
                position=position,
            )
        body = self.call("get_competitor_tracking")
        self.assertEqual(body["players"], ["Pro Brand", "Acme"])
        self.assertEqual(body["google_positions_by_keyword"][0]["positions"], {"Pro Brand": 12, "Acme": 3})
        self.assertEqual(body["seo_google"][1]["keywords_in_google_top10"], 1)

    def test_keywords_tool_reads_the_stored_snapshot(self):
        KeywordSnapshot.objects.create(
            program=self.program,
            kind="gaps",
            rows=[
                {
                    "keyword": "geo audit",
                    "search_volume": 900,
                    "rivals": [{"domain": "acme.io", "position": 2}],
                }
            ],
        )
        body = self.call("get_keywords")
        self.assertEqual(
            body["keyword_gaps"][0],
            {
                "keyword": "geo audit",
                "monthly_searches": 900,
                "who_ranks": [{"domain": "acme.io", "position": 2}],
                "new": None,
            },
        )

    def test_site_health_lists_recent_changes(self):
        AgentActionLog.objects.create(
            program=self.program, action=watch.ANOMALY, outcome="blocked", detail={"kind": "site_down"}
        )
        AgentActionLog.objects.create(
            program=self.program, action=watch.CHECKED, outcome="succeeded", detail={"state": {"down": True}}
        )
        body = self.call("get_site_health")
        self.assertEqual(body["changes_last_30_days"][0]["kind"], "site_down")
        self.assertTrue(body["state"]["down"])

    def test_check_now_tools_queue_once_a_day(self):
        with mock.patch("apps.growth_agent.analysis_tasks.dispatch_rank_check"):
            self.assertTrue(self.call("check_ranks_now")["started"])
            self.assertFalse(self.call("check_ranks_now")["started"])
