"""Tests for the GEO-signal task generator (services/geo_tasks.py)."""

from unittest.mock import patch

from django.test import TestCase

from apps.analyzer.models import (
    AnalysisRun,
    Competitor,
    PageScore,
    PromptCitation,
    PromptResult,
    PromptTrack,
    Recommendation,
)
from apps.analyzer.services.geo_tasks import (
    CODE_CITATION_GAP,
    CODE_COMPETITOR_CITED,
    CODE_COMPETITOR_PILLAR_GAP,
    CODE_PROMPT_LOST,
    generate_geo_signal_tasks,
    sync_geo_signal_tasks,
)


class GeoTaskGenerationTests(TestCase):
    def setUp(self):
        self.run = AnalysisRun.objects.create(url="https://brand.com", brand_name="Brand")

    def _lost_prompt(self, text: str, *, competitor_domain: str | None = None):
        track = PromptTrack.objects.create(analysis_run=self.run, prompt_text=text)
        for engine in (PromptResult.Engine.CHATGPT, PromptResult.Engine.GEMINI):
            res = PromptResult.objects.create(
                prompt_track=track, engine=engine, brand_mentioned=False,
            )
            if competitor_domain:
                PromptCitation.objects.create(
                    prompt_result=res, url=f"https://{competitor_domain}/x",
                    domain=competitor_domain, is_competitor=True,
                )
        return track

    def test_no_prompts_returns_empty(self):
        self.assertEqual(generate_geo_signal_tasks(self.run), [])

    def test_prompt_with_a_mention_is_not_lost(self):
        track = PromptTrack.objects.create(analysis_run=self.run, prompt_text="best crm")
        PromptResult.objects.create(
            prompt_track=track, engine=PromptResult.Engine.CHATGPT, brand_mentioned=True,
        )
        tasks = generate_geo_signal_tasks(self.run)
        self.assertFalse(any(t["finding_code"] == CODE_PROMPT_LOST for t in tasks))

    def test_lost_prompt_produces_grounded_task(self):
        self._lost_prompt("best project tool for agencies")
        tasks = generate_geo_signal_tasks(self.run)
        lost = [t for t in tasks if t["finding_code"] == CODE_PROMPT_LOST]
        self.assertEqual(len(lost), 1)
        t = lost[0]
        self.assertIn("best project tool for agencies", t["evidence"]["prompt"])
        self.assertEqual(t["source"], Recommendation.Source.GEO_SIGNAL)
        self.assertGreater(t["impact_points"], 0.0)
        self.assertNotIn("%", t["impact_estimate"])

    def test_competitor_citation_task(self):
        self._lost_prompt("best crm software", competitor_domain="rival.com")
        tasks = generate_geo_signal_tasks(self.run)
        comp = [t for t in tasks if t["finding_code"] == CODE_COMPETITOR_CITED]
        self.assertEqual(len(comp), 1)
        self.assertIn("rival.com", comp[0]["evidence"]["competitor_domains"])

    def _lost_with_gap_domain(self, domain: str) -> None:
        track = PromptTrack.objects.create(analysis_run=self.run, prompt_text="best framework")
        for engine in (PromptResult.Engine.CHATGPT, PromptResult.Engine.GEMINI):
            res = PromptResult.objects.create(
                prompt_track=track, engine=engine, brand_mentioned=False,
            )
            PromptCitation.objects.create(
                prompt_result=res, url=f"https://{domain}/x", domain=domain,
                is_competitor=False, is_brand=False,
            )

    def test_platform_domains_excluded_from_citation_gap(self):
        # Vercel is obviously all over Medium — "get mentioned on medium.com" is junk.
        self._lost_with_gap_domain("medium.com")
        self._lost_with_gap_domain("youtube.com")
        tasks = generate_geo_signal_tasks(self.run)
        self.assertFalse(any(t["finding_code"] == CODE_CITATION_GAP for t in tasks))

    def test_authority_domain_still_produces_citation_gap(self):
        self._lost_with_gap_domain("techcrunch.com")
        # Presence unknown (no Serper) → keep the task.
        with patch("apps.analyzer.services.geo_tasks.brand_present_on_domain", return_value=None):
            tasks = generate_geo_signal_tasks(self.run)
        gaps = [t for t in tasks if t["finding_code"] == CODE_CITATION_GAP]
        self.assertEqual(len(gaps), 1)
        self.assertIn("techcrunch.com", gaps[0]["title"])

    def test_citation_gap_suppressed_when_brand_already_present(self):
        # The general, per-brand fix: brand already on the domain → not a gap.
        self._lost_with_gap_domain("techcrunch.com")
        with patch("apps.analyzer.services.geo_tasks.brand_present_on_domain", return_value=True):
            tasks = generate_geo_signal_tasks(self.run)
        self.assertFalse(any(t["finding_code"] == CODE_CITATION_GAP for t in tasks))

    def test_competitor_pillar_gap_task(self):
        PageScore.objects.create(analysis_run=self.run, composite_score=40.0)
        Competitor.objects.create(
            analysis_run=self.run, name="Rival", url="https://rival.com",
            scored=True, composite_score=65.0,
        )
        tasks = generate_geo_signal_tasks(self.run)
        gap = [t for t in tasks if t["finding_code"] == CODE_COMPETITOR_PILLAR_GAP]
        self.assertEqual(len(gap), 1)
        self.assertEqual(gap[0]["evidence"]["gap"], 25.0)

    def test_small_pillar_gap_is_ignored(self):
        PageScore.objects.create(analysis_run=self.run, composite_score=60.0)
        Competitor.objects.create(
            analysis_run=self.run, name="Rival", url="https://rival.com",
            scored=True, composite_score=63.0,
        )
        tasks = generate_geo_signal_tasks(self.run)
        self.assertFalse(any(t["finding_code"] == CODE_COMPETITOR_PILLAR_GAP for t in tasks))


class GeoTaskSyncTests(TestCase):
    def setUp(self):
        self.run = AnalysisRun.objects.create(url="https://brand.com", brand_name="Brand")
        track = PromptTrack.objects.create(analysis_run=self.run, prompt_text="best tool")
        PromptResult.objects.create(
            prompt_track=track, engine=PromptResult.Engine.CHATGPT, brand_mentioned=False,
        )

    def test_sync_is_idempotent_replace(self):
        n1 = sync_geo_signal_tasks(self.run)
        self.assertGreater(n1, 0)
        base = Recommendation.objects.filter(
            analysis_run=self.run, source=Recommendation.Source.GEO_SIGNAL
        ).count()
        # Second sync replaces, does not duplicate.
        sync_geo_signal_tasks(self.run)
        after = Recommendation.objects.filter(
            analysis_run=self.run, source=Recommendation.Source.GEO_SIGNAL
        ).count()
        self.assertEqual(base, after)

    def test_sync_removes_stale_geo_user_actions(self):
        from apps.analyzer.models import UserAction

        sync_geo_signal_tasks(self.run)
        geo_rec = Recommendation.objects.filter(
            analysis_run=self.run, source=Recommendation.Source.GEO_SIGNAL
        ).first()
        # Simulate the task being materialized into the dashboard.
        UserAction.objects.create(
            user_email="u@x.com", analysis_run=self.run, recommendation=geo_rec,
            action_type=UserAction.ActionType.BUILD_BACKLINKS, title=geo_rec.title,
        )
        # Re-sync (as the daily prompt recheck would) must not leave an orphaned task.
        sync_geo_signal_tasks(self.run)
        self.assertEqual(
            UserAction.objects.filter(analysis_run=self.run, recommendation__isnull=True).count(),
            0,
        )

    def test_sync_does_not_touch_analyzer_recs(self):
        Recommendation.objects.create(
            analysis_run=self.run, pillar="content", priority="high",
            title="On-page task", description="", action="", category="content",
            source=Recommendation.Source.ANALYZER,
        )
        sync_geo_signal_tasks(self.run)
        self.assertTrue(
            Recommendation.objects.filter(
                analysis_run=self.run, source=Recommendation.Source.ANALYZER
            ).exists()
        )
