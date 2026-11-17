"""
Weekly SEO / GEO / AIO tracking: tracked keywords, Google positions (Serper),
AI Overview citations (DataForSEO), and the weekly report. Providers are faked.

Run: python manage.py test apps.growth_agent.tests.test_rank_tracking --settings=config.settings.test
"""

from datetime import date, timedelta
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from apps.analyzer.models import AnalysisRun, Competitor
from apps.integrations.services.dataforseo import DataForSEOError

from ..models import KeywordSnapshot, PositioningReport, RankObservation, TrackedKeyword, WeeklySnapshot
from ..services.rank_tracking import check, rules
from ..services.rank_tracking.report import report
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_execution_flow import ExecutionTestCase


class RuleTests(SimpleTestCase):
    def test_week_starts_on_monday(self):
        self.assertEqual(rules.week_of(date(2026, 11, 19)), date(2026, 11, 16))

    def test_keywords_are_picked_brand_first_and_removed_ones_never_come_back(self):
        picked = rules.pick_keywords(
            brand_rows=[{"keyword": "AI Visibility"}, {"keyword": "geo tool"}],
            gap_rows=[
                {"keyword": "small", "search_volume": 10},
                {"keyword": "big gap", "search_volume": 900},
            ],
            prompts=["best geo tool for agencies", "a very long prompt that has far too many words to track"],
            taken={"geo tool"},
            room=4,
        )
        self.assertEqual(picked, ["ai visibility", "big gap", "small", "best geo tool for agencies"])

    def test_positions_and_citations_match_subdomains(self):
        organic = [
            {"link": "https://blog.acme.io/x", "position": 1},
            {"link": "https://www.pro.example/y", "position": 2},
        ]
        self.assertEqual(
            rules.positions(organic, ["pro.example", "acme.io", "beta.com"]),
            {"pro.example": 2, "acme.io": 1, "beta.com": None},
        )
        self.assertEqual(
            rules.cited(["acme.io"], ["pro.example", "acme.io"]), {"pro.example": False, "acme.io": True}
        )

    def test_week_summaries(self):
        rows = [
            {"domain": "a.com", "position": 3, "cited": True},
            {"domain": "a.com", "position": 40, "cited": False},
            {"domain": "b.com", "position": None, "cited": False},
        ]
        self.assertEqual(
            rules.seo_week_summary(rows, ["a.com", "b.com"])["a.com"],
            {"top10": 1, "ranked": 2, "average": 21.5},
        )
        self.assertEqual(rules.aio_week_summary(rows, ["a.com", "b.com"]), {"a.com": 1, "b.com": 0})


class RankCheckTests(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        run = AnalysisRun.objects.create(
            organization=self.pro_org,
            url=self.pro_org.url,
            email=PRO_EMAIL,
            status=AnalysisRun.Status.COMPLETE,
        )
        Competitor.objects.create(analysis_run=run, name="Acme", url="https://acme.io")
        PositioningReport.objects.create(
            program=self.program,
            analysis_run=run,
            status=PositioningReport.Status.COMPLETE,
            lost_prompts=[{"prompt": "best geo tool", "winning_rivals": [{"name": "Acme"}]}],
        )
        KeywordSnapshot.objects.create(
            program=self.program, kind="brand", rows=[{"keyword": "ai visibility"}]
        )
        self.base = f"/api/growth/programs/{self.pro_org.slug}/rank-tracking/"

    def serp(self, keyword, num):
        return {
            "organic": [
                {"link": "https://acme.io/a", "position": 1},
                {"link": "https://pro.example/b", "position": 4},
            ]
        }

    def run_check(self, aio=None):
        aio = aio if aio is not None else mock.Mock(return_value=["acme.io"])
        with (
            mock.patch.object(check.serper, "search", side_effect=self.serp),
            mock.patch.object(check, "fetch_ai_overview_domains", aio),
        ):
            return check.run_check(self.program)

    def test_a_check_tracks_keywords_and_stores_positions_and_citations(self):
        self.run_check()
        keywords = set(TrackedKeyword.objects.values_list("keyword", flat=True))
        self.assertEqual(keywords, {"ai visibility", "best geo tool"})
        seo = RankObservation.objects.filter(kind="seo", keyword="ai visibility")
        self.assertEqual({o.domain: o.position for o in seo}, {"pro.example": 4, "acme.io": 1})
        aio = RankObservation.objects.filter(kind="aio", keyword="ai visibility")
        self.assertEqual({o.domain: o.cited for o in aio}, {"pro.example": False, "acme.io": True})

    def test_rerunning_the_same_week_replaces_rows(self):
        self.run_check()
        self.run_check()
        self.assertEqual(RankObservation.objects.filter(kind="seo").count(), 4)

    def test_no_dataforseo_credits_keeps_seo_and_explains_aio(self):
        self.run_check(aio=mock.Mock(side_effect=DataForSEOError("serp: 402 Payment Required")))
        self.assertFalse(RankObservation.objects.filter(kind="aio").exists())
        self.assertTrue(RankObservation.objects.filter(kind="seo").exists())
        self.assertIn("waiting for DataForSEO credits", report(self.program)["aio_note"])

    def test_report_compares_with_last_week_and_reads_geo_snapshots(self):
        last_week = rules.week_of(timezone.localdate()) - timedelta(days=7)
        RankObservation.objects.create(
            program=self.program,
            week=last_week,
            kind="seo",
            keyword="ai visibility",
            domain="pro.example",
            position=9,
        )
        self.run_check()
        WeeklySnapshot.objects.create(
            cycle=self.cycle,
            week=1,
            status=WeeklySnapshot.Status.COMPLETE,
            share_of_voice=12.5,
            rivals={"Acme": 40.0},
            completed_at=timezone.now(),
        )
        body = report(self.program)
        self.assertEqual([p["domain"] for p in body["players"]], ["pro.example", "acme.io"])
        row = next(r for r in body["seo"]["latest"] if r["keyword"] == "ai visibility")
        self.assertEqual(row["domains"]["pro.example"], {"now": 4, "before": 9})
        self.assertEqual(len(body["seo"]["weeks"]), 2)
        self.assertEqual(body["geo"][0]["rivals"], {"Acme": 40.0})

    def test_owner_adds_and_removes_keywords_and_removed_ones_stay_out(self):
        self._body = {"keyword": "  GEO Agency  "}
        added = self.signed("POST", f"{self.base}keywords/", PRO_EMAIL).json()
        keyword = next(k for k in added["keywords"] if k["keyword"] == "geo agency")
        self.assertEqual(keyword["source"], "owner")
        self.signed("POST", f"{self.base}keywords/{keyword['id']}/remove/", PRO_EMAIL)
        check.fill_keywords(self.program)
        self.assertFalse(TrackedKeyword.objects.get(keyword="geo agency").active)

    def test_check_now_at_most_daily_and_owner_only(self):
        with mock.patch("apps.growth_agent.analysis_tasks.dispatch_rank_check"):
            self.assertEqual(self.signed("POST", f"{self.base}check/", PRO_EMAIL).status_code, 202)
            self.assertEqual(self.signed("POST", f"{self.base}check/", PRO_EMAIL).status_code, 409)
        self.assertIn(self.signed("GET", self.base, OTHER_EMAIL).status_code, (403, 404))
        self._body = {"keyword": "x"}
        self.assertIn(self.signed("POST", f"{self.base}keywords/", OTHER_EMAIL).status_code, (403, 404))
