"""Competitive prompts are generated on request, never on every run.

Firing them automatically at the end of each analysis cost ~$0.75 - **39% of the
entire cost of an analysis** - to produce rows that no shipped UI displays. The
frontend's `generateCompetitorPrompts` even pointed at a generate route that did
not exist server-side, which is a fair summary of how used the feature was.

The work itself is unchanged: a caller gets the same prompts and the same engine
answers. Only the trigger moved.
"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.analyzer.models import AnalysisRun, PromptTrack
from apps.organizations.models import Organization

_FIRE = "apps.analyzer.tasks._generate_and_fire_competitive_prompts"


class NoAutoFireTests(TestCase):
    def test_a_completed_run_does_not_fire_competitive_prompts(self):
        """The regression: this was 39% of every analysis, for nobody."""
        import inspect

        from apps.analyzer import tasks

        source = inspect.getsource(tasks.run_single_page_analysis)
        self.assertNotIn("_generate_and_fire_competitive_prompts(run)", source)

    def test_the_generator_itself_is_still_available(self):
        """Removed from the run, not deleted — the endpoint calls it."""
        from apps.analyzer import tasks

        self.assertTrue(callable(tasks._generate_and_fire_competitive_prompts))


class GenerateEndpointTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", owner_email="o@acme.com")
        self.run = AnalysisRun.objects.create(
            url="https://acme.com", organization=self.org, email="o@acme.com", brand_name="Acme"
        )

    def _url(self, slug=None):
        return reverse("analyzer:competitor-prompts-generate", args=[slug or self.run.slug])

    def test_posting_dispatches_generation(self):
        with patch(_FIRE) as fire:
            resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["status"], "generating")
        fire.assert_called_once()

    def test_a_second_call_is_a_cheap_no_op_once_saturated(self):
        for i in range(10):
            PromptTrack.objects.create(
                analysis_run=self.run,
                prompt_text=f"q{i}",
                prompt_type=PromptTrack.PromptSurfaceType.COMPETITIVE,
                is_custom=False,
            )
        with patch(_FIRE, side_effect=AssertionError("must not re-fire")) as fire:
            resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ready")
        self.assertEqual(resp.json()["count"], 10)
        fire.assert_not_called()

    def test_an_account_over_its_allowance_is_refused(self):
        from apps.analyzer.services import llm_spend

        AnalysisRun.objects.create(url="https://x.com", email="o@acme.com", llm_cost_usd=99.0)
        with patch.object(llm_spend, "limit_for", return_value=10.0):
            with patch(_FIRE, side_effect=AssertionError("must not fire")):
                resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, 402)

    def test_an_unknown_slug_is_404(self):
        with patch(_FIRE, side_effect=AssertionError("must not fire")):
            self.assertEqual(self.client.post(self._url("nope")).status_code, 404)

    def test_a_verified_stranger_cannot_spend_another_brands_budget(self):
        from apps.analyzer.tests.auth_helpers import signed_in

        with signed_in("stranger@example.com"), patch(
            _FIRE, side_effect=AssertionError("must not fire")
        ):
            self.assertEqual(self.client.post(self._url()).status_code, 404)

    def test_the_list_endpoint_still_returns_a_bare_array(self):
        """The shape the existing client parses; changing it would break it."""
        resp = self.client.get(
            reverse("analyzer:competitor-prompt-list", args=[self.run.slug])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)
