"""
Keyword data step 1: the brand's keywords and its gaps against the top 3
competitors, read weekly from DataForSEO. DataForSEO is faked: nothing is fetched.

Run: python manage.py test apps.growth_agent.tests.test_keywords --settings=config.settings.test
"""

from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.analyzer.models import AnalysisRun, Competitor
from apps.integrations.services.dataforseo import DataForSEOError, DataForSEONotConfigured

from ..models import AgentActionLog, KeywordSnapshot, PositioningReport
from ..services.keywords import gaps as rules
from ..services.keywords import refresh
from ..services.keywords.report import report
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_execution_flow import ExecutionTestCase

BRAND = rules.Rival("Pro Brand", "pro.example")
ACME = rules.Rival("Acme", "acme.io")
BETA = rules.Rival("Beta Tools", "betatools.com")


def kw(keyword, position=3, volume=500):
    return {"keyword": keyword, "position": position, "search_volume": volume, "url": "https://x/y"}


class GapRuleTests(SimpleTestCase):
    def test_rivals_ranked_by_how_often_ai_recommends_them(self):
        lost = [{"winning_rivals": [{"name": "Beta Tools"}]}, {"winning_rivals": [{"name": "beta tools"}]}]
        lost.append({"winning_rivals": [{"name": "Acme"}]})
        rivals = [ACME, BETA, rules.Rival("Gamma", ""), rules.Rival("Self", "pro.example")]
        self.assertEqual(rules.top_rivals(rivals, lost, own_domain="pro.example"), [BETA, ACME])

    def test_gaps_are_top_10_unbranded_keywords_the_brand_lacks_by_volume(self):
        brand_rows = [kw("ai visibility tool")]
        rival_rows = {
            ACME: [
                kw("ai visibility tool"),
                kw("geo audit", 2, 900),
                kw("acme login", 1, 5000),
                kw("deep", 40),
            ],
            BETA: [kw("geo audit", 5, 900), kw("llm seo guide", 8, 300), kw("tiny", 2, 3)],
        }
        rows = rules.gaps(brand_rows, rival_rows, brand=BRAND)
        self.assertEqual([r["keyword"] for r in rows], ["geo audit", "llm seo guide"])
        self.assertEqual([r["domain"] for r in rows[0]["rivals"]], ["acme.io", "betatools.com"])

    def test_whole_brand_names_only_so_real_keywords_are_kept(self):
        growth = rules.Rival("Growth Labs", "growthlabs.io")
        rows = rules.gaps([], {growth: [kw("growth marketing"), kw("growth labs pricing")]}, brand=BRAND)
        self.assertEqual([r["keyword"] for r in rows], ["growth marketing"])

    def test_new_gaps_are_marked_against_last_week(self):
        rows = rules.gaps([], {ACME: [kw("geo audit"), kw("llm seo")]}, brand=BRAND, previous={"geo audit"})
        self.assertEqual({r["keyword"]: r["new"] for r in rows}, {"geo audit": False, "llm seo": True})


@override_settings(DATAFORSEO_LOGIN="login", DATAFORSEO_PASSWORD="pw")
class KeywordRefreshTests(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        run = AnalysisRun.objects.create(
            organization=self.pro_org,
            url=self.pro_org.url,
            email=PRO_EMAIL,
            status=AnalysisRun.Status.COMPLETE,
        )
        for name, url in (("Acme", "https://acme.io"), ("Beta Tools", "https://www.betatools.com")):
            Competitor.objects.create(analysis_run=run, name=name, url=url)
        PositioningReport.objects.create(
            program=self.program,
            analysis_run=run,
            status=PositioningReport.Status.COMPLETE,
            lost_prompts=[{"winning_rivals": [{"name": "Acme"}]}],
        )
        self.base = f"/api/growth/programs/{self.pro_org.slug}/keywords/"

    def fake_fetch(self, domain, limit):
        return {
            "pro.example": [kw("ai visibility tool")],
            "acme.io": [kw("geo audit", 2, 900)],
            "betatools.com": [kw("llm seo guide", 4, 300)],
        }[domain]

    def test_a_refresh_stores_keywords_and_gaps(self):
        with mock.patch.object(refresh, "fetch_ranked_keywords", side_effect=self.fake_fetch) as fetch:
            self.assertTrue(refresh.run_refresh(self.program))
        self.assertEqual(fetch.call_count, 3)  # brand + 2 competitors
        body = report(self.program)
        self.assertEqual(body["keywords"][0]["keyword"], "ai visibility tool")
        self.assertEqual([g["keyword"] for g in body["gaps"]], ["geo audit", "llm seo guide"])
        self.assertEqual(body["competitors"][0]["domain"], "acme.io")
        self.assertTrue(AgentActionLog.objects.filter(action=refresh.DONE, outcome="succeeded").exists())

    def test_a_failing_competitor_is_skipped_not_the_whole_read(self):
        def flaky(domain, limit):
            if domain == "acme.io":
                raise DataForSEOError("boom")
            return self.fake_fetch(domain, limit)

        with mock.patch.object(refresh, "fetch_ranked_keywords", side_effect=flaky):
            self.assertTrue(refresh.run_refresh(self.program))
        self.assertEqual([g["keyword"] for g in report(self.program)["gaps"]], ["llm seo guide"])

    def test_no_credits_stops_the_read_instead_of_storing_no_gaps(self):
        def broke(domain, limit):
            if domain == "pro.example":
                return [kw("ai visibility tool")]
            raise DataForSEOError("ranked_keywords: 402 Payment Required")

        with mock.patch.object(refresh, "fetch_ranked_keywords", side_effect=broke):
            self.assertFalse(refresh.run_refresh(self.program))
        self.assertFalse(KeywordSnapshot.objects.exists())
        self.assertIn("temporarily unavailable", report(self.program)["notes"][0])

    def test_missing_credentials_is_reported_not_raised(self):
        with mock.patch.object(refresh, "fetch_ranked_keywords", side_effect=DataForSEONotConfigured("x")):
            self.assertFalse(refresh.run_refresh(self.program))
        self.assertFalse(KeywordSnapshot.objects.exists())
        self.assertIn("isn't set up", report(self.program)["notes"][0])

    def test_without_an_analysis_nothing_is_fetched(self):
        PositioningReport.objects.all().delete()
        with mock.patch.object(refresh, "fetch_ranked_keywords") as fetch:
            self.assertFalse(refresh.run_refresh(self.program))
        fetch.assert_not_called()

    def test_refresh_now_runs_at_most_once_a_day_and_only_for_the_owner(self):
        with mock.patch("apps.growth_agent.analysis_tasks.dispatch_keyword_refresh"):
            first = self.signed("POST", f"{self.base}refresh/", PRO_EMAIL)
            second = self.signed("POST", f"{self.base}refresh/", PRO_EMAIL)
        self.assertEqual(first.status_code, 202)
        self.assertTrue(first.json()["refreshing"])
        self.assertEqual(second.status_code, 409)
        self.assertIn(self.signed("GET", self.base, OTHER_EMAIL).status_code, (403, 404))
        self.assertIn(self.signed("POST", f"{self.base}refresh/", OTHER_EMAIL).status_code, (403, 404))
