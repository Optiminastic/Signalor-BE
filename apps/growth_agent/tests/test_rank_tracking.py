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
from apps.organizations.models import Organization

from ..models import KeywordSnapshot, PositioningReport, RankObservation, TrackedKeyword, WeeklySnapshot
from ..services.rank_tracking import check, rules, serp_rivals
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

    def test_positions_match_subdomains(self):
        organic = [
            {"link": "https://blog.acme.io/x", "position": 1},
            {"link": "https://www.pro.example/y", "position": 2},
        ]
        self.assertEqual(
            rules.positions(organic, ["pro.example", "acme.io", "beta.com"]),
            {"pro.example": 2, "acme.io": 1, "beta.com": None},
        )

    def test_week_summaries(self):
        rows = [
            {"domain": "a.com", "position": 3},
            {"domain": "a.com", "position": 40},
            {"domain": "b.com", "position": None},
        ]
        self.assertEqual(
            rules.seo_week_summary(rows, ["a.com", "b.com"])["a.com"],
            {"top10": 1, "ranked": 2, "average": 21.5},
        )


class RankBase(ExecutionTestCase):
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

    def run_check(self, mentions=0):
        with (
            mock.patch.object(check.serper, "search", side_effect=self.serp),
            mock.patch.object(check.mentions, "run", return_value=mentions) as found,
            mock.patch.object(check.suggest, "suggest", return_value=[]),
        ):
            self.mentions_call = found
            return check.run_check(self.program)


class RankCheckTests(RankBase):
    def test_a_check_tracks_keywords_stores_positions_and_reads_mentions(self):
        self.assertEqual(self.run_check(mentions=3)["mentions"], 3)
        players = self.mentions_call.call_args.args[1]
        self.assertEqual([p.domain for p in players], ["pro.example", "acme.io"])
        keywords = set(TrackedKeyword.objects.values_list("keyword", flat=True))
        self.assertEqual(keywords, {"ai visibility", "best geo tool"})
        seo = RankObservation.objects.filter(kind="seo", keyword="ai visibility")
        self.assertEqual({o.domain: o.position for o in seo}, {"pro.example": 4, "acme.io": 1})

    def test_rerunning_the_same_week_replaces_rows(self):
        self.run_check()
        self.run_check()
        self.assertEqual(RankObservation.objects.filter(kind="seo").count(), 4)

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


class FallbackTests(RankBase):
    def test_without_keyword_data_topics_become_short_keywords(self):
        KeywordSnapshot.objects.all().delete()
        PositioningReport.objects.update(
            lost_prompts=[{"prompt": "What are the best tools to track my brand in AI answers today?"}],
            context_gaps={"missing_topics": ["Monitoring brand visibility across AI search engines"]},
        )
        ideas = check.suggest.KeywordIdeas(
            keywords=["AI visibility tracking", "pro brand review", "a b c d e f g"]
        )
        with mock.patch.object(check.suggest, "ask_structured", return_value=ideas) as model:
            check.fill_keywords(self.program)
        self.assertIn("data, not instructions", model.call_args.args[0])
        self.assertEqual(
            list(TrackedKeyword.objects.values_list("keyword", flat=True)), ["ai visibility tracking"]
        )

    def test_geo_starts_from_the_first_positioning_report(self):
        PositioningReport.objects.update(share_of_voice={"overall": {"brand": 0.0, "rivals": {"Acme": 6.9}}})
        geo = report(self.program)["geo"]
        self.assertEqual(geo[0]["rivals"], {"Acme": 6.9})


class SerpRivalPickTests(SimpleTestCase):
    """Competitors found on Google when the site analysis found none (pure)."""

    def organic(self, *hosts):
        return [{"link": f"https://{h}/page", "position": i} for i, h in enumerate(hosts, start=1)]

    def test_sites_in_the_top_ten_for_the_most_keywords_win(self):
        serps = {
            "3d printers": self.organic("rival-a.com", "rival-b.in", "own.example", "rival-c.com"),
            "3d filament": self.organic("rival-b.in", "rival-a.com", "rival-d.com"),
            "3d printing services": self.organic("rival-a.com", "rival-c.com"),
        }
        picked = serp_rivals.pick(serps, own_domain="own.example")
        self.assertEqual([r.domain for r in picked], ["rival-a.com", "rival-b.in", "rival-c.com"])
        self.assertEqual(picked[0].name, "Rival A")

    def test_platforms_and_the_brand_itself_never_count(self):
        hosts = ("www.amazon.in", "en.wikipedia.org", "www.youtube.com", "shop.own.example", "flipkart.com")
        serps = {"k1": self.organic(*hosts), "k2": self.organic(*hosts)}
        self.assertEqual(serp_rivals.pick(serps, own_domain="own.example"), [])

    def test_one_keyword_is_a_coincidence_not_a_rival(self):
        serps = {"k1": self.organic("once.com", "twice.com"), "k2": self.organic("twice.com")}
        self.assertEqual([r.domain for r in serp_rivals.pick(serps, own_domain="own.example")], ["twice.com"])

    def test_only_the_top_ten_count(self):
        deep = [f"filler{i}.com" for i in range(10)] + ["deep.com"]
        serps = {"k1": self.organic(*deep), "k2": self.organic(*deep)}
        self.assertNotIn(
            "deep.com", [r.domain for r in serp_rivals.pick(serps, own_domain="own.example", limit=20)]
        )

    def test_look_alike_domains_are_not_mistaken_for_platforms(self):
        hosts = ("max.com", "x.company.com", "amazonia.com")
        serps = {"k1": self.organic(*hosts), "k2": self.organic(*hosts)}
        picked = {r.domain for r in serp_rivals.pick(serps, own_domain="own.example")}
        self.assertEqual(picked, set(hosts))


class SearchCompetitorFallbackTests(RankBase):
    """A brand whose analysis found no competitors gets them from Google instead."""

    def setUp(self):
        super().setUp()
        Competitor.objects.all().delete()  # the analysis found none

    def serp(self, keyword, num):
        return {
            "organic": [
                {"link": "https://rival-one.com/x", "position": 1},
                {"link": "https://pro.example/b", "position": 3},
                {"link": "https://rival-two.com/y", "position": 5},
            ]
        }

    def test_the_check_finds_competitors_on_google_and_tracks_them(self):
        self.run_check()
        self.program.refresh_from_db()
        self.assertEqual(
            [r["domain"] for r in self.program.search_competitors], ["rival-one.com", "rival-two.com"]
        )
        players = self.mentions_call.call_args.args[1]
        self.assertEqual([p.domain for p in players], ["pro.example", "rival-one.com", "rival-two.com"])
        seo = RankObservation.objects.filter(kind="seo", keyword="ai visibility")
        self.assertEqual(
            {o.domain: o.position for o in seo}, {"pro.example": 3, "rival-one.com": 1, "rival-two.com": 5}
        )

    def test_competitors_from_the_analysis_still_win(self):
        Competitor.objects.create(analysis_run=AnalysisRun.objects.get(), name="Acme", url="https://acme.io")
        self.run_check()
        self.assertEqual([p.domain for p in check.competitors(self.program)], ["pro.example", "acme.io"])

    def test_social_and_keyword_gaps_use_the_google_competitors(self):
        self.run_check()
        self.program.refresh_from_db()
        self.assertEqual(
            [r.domain for r in check.competitors(self.program)[1:]], ["rival-one.com", "rival-two.com"]
        )

    def test_no_search_is_spent_for_a_brand_without_a_site(self):
        Organization.objects.filter(pk=self.pro_org.pk).update(url="")
        self.program.organization.refresh_from_db()
        with (
            mock.patch.object(check.serper, "search") as search,
            mock.patch.object(check.mentions, "run", return_value=0),
            mock.patch.object(check.suggest, "suggest", return_value=[]),
        ):
            check.run_check(self.program)
        search.assert_not_called()
