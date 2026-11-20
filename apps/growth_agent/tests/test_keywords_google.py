"""
Keywords from Google results (the weekly rank check), for brands without a
keyword data provider.

Run: python manage.py test apps.growth_agent.tests.test_keywords_google --settings=config.settings.test
"""

from django.test import override_settings
from django.utils import timezone

from ..models import AgentActionLog, KeywordSnapshot, RankObservation
from ..services.keywords import google_source, refresh, report
from ..services.keywords.gaps import Rival
from ..services.rank_tracking import rules
from .test_execution_flow import ExecutionTestCase

BRAND, RIVAL = Rival("Brand", "brand.com"), Rival("Rival Co", "rivalco.com")


@override_settings(DATAFORSEO_LOGIN="")
class GoogleKeywordTests(ExecutionTestCase):
    def observe(self, keyword, domain, position):
        RankObservation.objects.create(
            program=self.program,
            week=rules.week_of(timezone.localdate()),
            kind=RankObservation.Kind.SEO,
            keyword=keyword,
            domain=domain,
            position=position,
        )

    def test_ranked_keywords_and_competitor_wins(self):
        self.observe("3d printer india", "brand.com", 4)
        self.observe("3d printer india", "rivalco.com", 2)
        self.observe("pla filament", "brand.com", 35)
        self.observe("pla filament", "rivalco.com", 6)
        self.observe("resin printer", "rivalco.com", 9)
        brand_rows, gaps = google_source.build(self.program, [BRAND, RIVAL])
        self.assertEqual(
            [(r["keyword"], r["position"]) for r in brand_rows],
            [("3d printer india", 4), ("pla filament", 35)],
        )
        self.assertEqual([g["keyword"] for g in gaps], ["pla filament", "resin printer"])
        self.assertEqual(gaps[0]["rivals"], [{"name": "Rival Co", "domain": "rivalco.com", "position": 6}])

    def test_nothing_observed_stores_nothing(self):
        self.assertFalse(google_source.store(self.program, [BRAND, RIVAL]))
        self.assertFalse(KeywordSnapshot.objects.exists())

    def test_the_tab_explains_the_source_instead_of_saying_not_set_up(self):
        self.observe("pla filament", "brand.com", 12)
        google_source.store(self.program, [BRAND, RIVAL])
        body = report.report(self.program)
        self.assertEqual(body["source"], "google")
        self.assertEqual(len(body["keywords"]), 1)
        self.assertIn("From Google results", body["notes"][0])

    def test_a_refresh_without_a_provider_uses_google_results(self):
        self.observe("pla filament", "brand.com", 12)
        from unittest import mock

        with (
            mock.patch.object(
                refresh,
                "latest_run",
                return_value=(
                    mock.Mock(**{"competitors.all.return_value.order_by.return_value": []}),
                    mock.Mock(lost_prompts=[]),
                ),
            ),
            mock.patch(
                "apps.growth_agent.services.rank_tracking.check.competitors", return_value=[BRAND, RIVAL]
            ),
            mock.patch.object(refresh, "_brand_keywords", side_effect=refresh.DataForSEONotConfigured),
        ):
            self.assertTrue(refresh.run_refresh(self.program))
        done = AgentActionLog.objects.get(action=refresh.DONE)
        self.assertEqual((done.outcome, done.detail["source"]), ("succeeded", "google"))
