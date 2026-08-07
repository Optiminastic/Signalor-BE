"""The 30-day projection endpoint: shape, scoping, and the projection maths.

The maths is made deterministic by giving the brand a single completed run, so
momentum is 0 and the projected gain reduces to the opportunity term alone
(``headroom * weight * weak_ratio``) - an exact, assertable number rather than a
trend extrapolation.
"""

from django.test import TestCase

from apps.analyzer.models import (
    AnalysisRun,
    Competitor,
    PromptResult,
    PromptTrack,
)
from apps.organizations.models import Organization


class ProjectionEndpointTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Acme", owner_email="owner@acme.com", url="https://acme.com"
        )
        # Current visibility 20 → headroom 80.
        self.run = AnalysisRun.objects.create(
            url="https://acme.com",
            organization=self.org,
            email="owner@acme.com",
            status="complete",
            composite_score=20.0,
        )
        # Prompts on a 0-100 scale: two below the 50 midpoint → 2 weak of 4.
        for score in (10.0, 30.0, 70.0, 90.0):
            track = PromptTrack.objects.create(
                analysis_run=self.run, prompt_text=f"prompt {score}", score=score
            )
        # Four prompt results, exactly one recommended (mentioned + positive) → 25%.
        first_track = self.run.prompt_tracks.first()
        PromptResult.objects.create(
            prompt_track=first_track,
            engine="chatgpt",
            brand_mentioned=True,
            sentiment=PromptResult.Sentiment.POSITIVE,
        )
        for _ in range(3):
            PromptResult.objects.create(
                prompt_track=first_track,
                engine="chatgpt",
                brand_mentioned=False,
                sentiment=PromptResult.Sentiment.NEUTRAL,
            )
        # One rival below (not ahead), one within reach, one out of reach.
        Competitor.objects.create(analysis_run=self.run, name="BelowCo", url="https://below.com", composite_score=10.0)
        Competitor.objects.create(analysis_run=self.run, name="NearCo", url="https://near.com", composite_score=25.0)
        Competitor.objects.create(analysis_run=self.run, name="FarCo", url="https://far.com", composite_score=90.0)

    def _get(self, slug):
        return self.client.get(f"/api/analyzer/runs/s/{slug}/projection/")

    def test_returns_the_full_projection_shape(self):
        resp = self._get(self.run.slug)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["window_days"], 30)
        self.assertEqual(
            set(body),
            {"window_days", "generated_at", "visibility", "recommendation", "competitors", "prompts"},
        )

    def test_visibility_projection_is_the_opportunity_uplift(self):
        # headroom 80, weak ratio 0.5, weight 0.4 → gain 16, target 36.
        body = self._get(self.run.slug).json()
        self.assertEqual(body["visibility"], {"current": 20, "target": 36, "delta": 16})

    def test_competitors_within_reach_of_the_target(self):
        body = self._get(self.run.slug).json()
        competitors = body["competitors"]
        # Two rivals sit above 20 (NearCo 25, FarCo 90); only NearCo is <= target 36.
        self.assertEqual(competitors["total_ahead"], 2)
        self.assertEqual(competitors["to_pass"], 1)
        self.assertEqual(competitors["names"], ["NearCo"])

    def test_weak_prompt_count(self):
        body = self._get(self.run.slug).json()
        self.assertEqual(body["prompts"], {"to_improve": 2, "total": 4})

    def test_recommendation_projection(self):
        # 1 of 4 results recommended → 25%; lift = round(75 * 0.4 * 0.5) = 15 → 40.
        body = self._get(self.run.slug).json()
        self.assertEqual(body["recommendation"], {"current": 25, "target": 40, "delta": 15})

    def test_soft_deleted_prompts_are_ignored(self):
        from django.utils import timezone

        self.run.prompt_tracks.filter(score=10.0).update(deleted_at=timezone.now())
        body = self._get(self.run.slug).json()
        # One weak prompt removed → 1 weak of 3.
        self.assertEqual(body["prompts"], {"to_improve": 1, "total": 3})

    def test_maxed_out_brand_has_no_headroom(self):
        maxed = AnalysisRun.objects.create(
            url="https://acme.com/max",
            organization=self.org,
            email="owner@acme.com",
            status="complete",
            composite_score=100.0,
        )
        body = self._get(maxed.slug).json()
        self.assertEqual(body["visibility"], {"current": 100, "target": 100, "delta": 0})
        self.assertEqual(body["competitors"]["to_pass"], 0)

    def test_unknown_slug_is_404(self):
        self.assertEqual(self._get("does-not-exist").status_code, 404)
