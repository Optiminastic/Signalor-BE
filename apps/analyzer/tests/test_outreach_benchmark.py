"""The public outreach-benchmark endpoints.

The contract that matters most is the gate. This endpoint has no login by
design, and every successful POST spends LLM credits, so "unset key" and "wrong
key" must both refuse — otherwise finding the URL is the same as finding the
budget.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.analyzer.models import AnalysisRun, PromptResult, PromptTrack
from apps.analyzer.services import outreach_benchmark as ob

KEY = "test-outreach-key"
_TASK = "apps.analyzer.views.outreach.queue"


@override_settings(OUTREACH_BENCHMARK_KEY=KEY)
class OutreachCreateGateTests(TestCase):
    def _post(self, payload, key=KEY):
        headers = {"x-outreach-key": key} if key is not None else {}
        return self.client.post(
            "/api/analyzer/outreach/", payload, content_type="application/json", headers=headers
        )

    def test_valid_key_starts_a_benchmark_run(self):
        with patch("core.queue.is_eager", return_value=False), patch("core.queue.send") as send:
            resp = self._post({"url": "https://example.com"})

        self.assertEqual(resp.status_code, 201, resp.content)
        run = AnalysisRun.objects.get(slug=resp.json()["slug"])
        self.assertEqual(run.run_type, AnalysisRun.RunType.OUTREACH)
        self.assertEqual(run.status, AnalysisRun.Status.PENDING)
        send.assert_called_once()

    def test_missing_key_is_refused_and_spends_nothing(self):
        with patch("core.queue.send") as send:
            resp = self._post({"url": "https://example.com"}, key=None)

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(AnalysisRun.objects.exists())
        send.assert_not_called()

    def test_wrong_key_is_refused(self):
        resp = self._post({"url": "https://example.com"}, key="nope")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(AnalysisRun.objects.exists())

    @override_settings(OUTREACH_BENCHMARK_KEY="")
    def test_unset_key_disables_generation_entirely(self):
        """The safe default: an environment that never opted in cannot be billed."""
        resp = self._post({"url": "https://example.com"}, key="")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(AnalysisRun.objects.exists())

    def test_bare_domain_gets_a_scheme(self):
        with patch("core.queue.is_eager", return_value=False), patch("core.queue.send"):
            resp = self._post({"url": "acme.com"})

        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(AnalysisRun.objects.get().url, "https://acme.com")

    def test_private_address_is_rejected_by_the_ssrf_guard(self):
        resp = self._post({"url": "http://169.254.169.254/latest/meta-data/"})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(AnalysisRun.objects.exists())

    def test_missing_url_is_a_400(self):
        self.assertEqual(self._post({}).status_code, 400)

    def test_pinned_prompts_are_stored_for_the_run(self):
        with patch("core.queue.is_eager", return_value=False), patch("core.queue.send"):
            self._post({"url": "https://acme.com", "prompts": ["best MGA platforms", "  "]})

        self.assertEqual(AnalysisRun.objects.get().onboarding_prompts, ["best MGA platforms"])


class OutreachDetailTests(TestCase):
    def setUp(self):
        self.run = AnalysisRun.objects.create(
            url="https://acme.com",
            run_type=AnalysisRun.RunType.OUTREACH,
            status=AnalysisRun.Status.COMPLETE,
            progress=100,
            outreach_report={"prompts_total": 6, "opportunities": ["Do the thing."]},
        )

    def test_report_is_readable_without_a_key(self):
        """Reads stay open so a finished benchmark can be sent to the prospect."""
        resp = self.client.get(f"/api/analyzer/outreach/{self.run.slug}/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["report"]["opportunities"], ["Do the thing."])

    def test_unknown_slug_is_404(self):
        self.assertEqual(self.client.get("/api/analyzer/outreach/nope/").status_code, 404)

    def test_non_outreach_run_is_not_exposed_here(self):
        other = AnalysisRun.objects.create(
            url="https://private.com", run_type=AnalysisRun.RunType.SINGLE_PAGE
        )
        self.assertEqual(self.client.get(f"/api/analyzer/outreach/{other.slug}/").status_code, 404)


class FindingsRollupTests(TestCase):
    """The numbers the email quotes, derived only from what was persisted."""

    def setUp(self):
        self.run = AnalysisRun.objects.create(
            url="https://acme.com", run_type=AnalysisRun.RunType.OUTREACH
        )

    def _track(self, text, engine_answers):
        track = PromptTrack.objects.create(analysis_run=self.run, prompt_text=text)
        for engine, (answered, mentioned) in engine_answers.items():
            PromptResult.objects.create(
                prompt_track=track,
                engine=engine,
                response_text="an answer" if answered else "",
                brand_mentioned=mentioned,
            )
        return track

    def test_lost_prompts_exclude_unmeasured_ones(self):
        """An engine failure must never be counted as a lost prompt."""
        self._track("won", {PromptResult.Engine.CHATGPT: (True, True)})
        self._track("lost", {PromptResult.Engine.CHATGPT: (True, False)})
        self._track("errored", {PromptResult.Engine.CHATGPT: (False, False)})

        findings = ob._findings(self.run)

        self.assertEqual(findings["prompts_total"], 3)
        self.assertEqual(findings["prompts_measured"], 2)
        self.assertEqual(findings["prompts_lost"], 1)

    def test_brand_and_industry_resolve_against_the_real_helpers(self):
        """Regression: these are imported lazily inside the function, so a wrong
        module path is invisible until a run is already crawling. It shipped once
        and failed live at 5% with an ImportError."""
        from bs4 import BeautifulSoup

        crawl = SimpleNamespace(
            soup=BeautifulSoup("<html><head><title>Acme Insurance</title></head></html>", "html.parser"),
            text="Acme sells policy administration software.",
            url="https://acme.com",
        )

        brand, industry = ob._brand_and_industry(crawl, self.run)

        self.assertTrue(brand)
        self.assertIsInstance(industry, str)

    def test_opportunities_are_skipped_when_nothing_was_measured(self):
        """No measurement means no grounded advice, so it must not invent any."""
        self._track("errored", {PromptResult.Engine.CHATGPT: (False, False)})

        findings = ob._findings(self.run)

        self.assertEqual(ob._opportunities("Acme", "saas", findings), [])
