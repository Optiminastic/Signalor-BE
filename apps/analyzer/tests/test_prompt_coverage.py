"""Tests for prompt → page coverage.

The report answers "for this prompt, do we even have a page?" - the question
that has to be settled before any on-page task or off-page outreach is worth
doing, because a prompt with no answering content cannot be fixed by improving
a page that does not exist.

The contract that matters most: an unindexed corpus reports *unknown*, never
*uncovered*. Conflating them would tell a customer to write pages they may
already have.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.analyzer.services import prompt_coverage as pc

_RETRIEVE = "apps.organizations.services.retrieval.retrieve"


def _chunk(url, score, heading=None, text="t"):
    return SimpleNamespace(
        text=text, source_url=url, heading_path=heading or [], score=score, metadata={}
    )


def _prompt(pk, text, intent="informational"):
    return SimpleNamespace(id=pk, prompt_text=text, intent=intent)


def _run():
    return SimpleNamespace(id=1, organization=SimpleNamespace(id=1))


def _coverage(chunks, prompts=None, indexed=True):
    with patch.object(pc, "_corpus_is_populated", return_value=indexed), patch(
        _RETRIEVE, return_value=chunks
    ):
        return pc.coverage_for_run(_run(), prompts=prompts or [_prompt(1, "what is GEO?")])


class ClassificationTests(SimpleTestCase):
    def test_strong_match_is_covered(self):
        rows = _coverage([_chunk("https://a.com/geo", 0.80)])
        self.assertEqual(rows[0].status, pc.COVERED)
        self.assertEqual(rows[0].best_url, "https://a.com/geo")

    def test_middling_match_is_weak(self):
        rows = _coverage([_chunk("https://a.com/blog", 0.50)])
        self.assertEqual(rows[0].status, pc.WEAK)

    def test_poor_match_is_uncovered(self):
        rows = _coverage([_chunk("https://a.com/pricing", 0.20)])
        self.assertEqual(rows[0].status, pc.UNCOVERED)

    def test_no_chunks_at_all_is_uncovered(self):
        rows = _coverage([])
        self.assertEqual(rows[0].status, pc.UNCOVERED)

    def test_every_status_carries_actionable_guidance(self):
        for score in (0.80, 0.50, 0.20):
            rows = _coverage([_chunk("https://a.com/x", score)])
            self.assertTrue(rows[0].guidance)

    def test_best_chunk_wins_not_the_first(self):
        rows = _coverage(
            [_chunk("https://a.com/weak", 0.30), _chunk("https://a.com/best", 0.90)]
        )
        self.assertEqual(rows[0].best_url, "https://a.com/best")

    def test_supporting_pages_exclude_the_winner(self):
        rows = _coverage(
            [
                _chunk("https://a.com/best", 0.90),
                _chunk("https://a.com/other", 0.70),
                _chunk("https://a.com/best", 0.65),  # same page, second chunk
            ]
        )
        self.assertEqual(rows[0].supporting_urls, ["https://a.com/other"])


class UnknownVsUncoveredTests(SimpleTestCase):
    """The distinction the whole report hinges on."""

    def test_unindexed_corpus_reports_unknown_not_uncovered(self):
        rows = _coverage([], indexed=False)
        self.assertEqual(rows[0].status, pc.UNKNOWN)
        self.assertNotEqual(rows[0].status, pc.UNCOVERED)

    def test_unknown_does_not_trigger_a_retrieval_call(self):
        with patch.object(pc, "_corpus_is_populated", return_value=False), patch(
            _RETRIEVE, side_effect=AssertionError("should not search an empty corpus")
        ):
            rows = pc.coverage_for_run(_run(), prompts=[_prompt(1, "q")])
        self.assertEqual(rows[0].status, pc.UNKNOWN)

    def test_unknown_prompts_are_excluded_from_the_percentage(self):
        """Otherwise a missing index reads as bad coverage."""
        rows = [
            pc.PromptCoverage(1, "a", "", pc.COVERED, best_score=0.9),
            pc.PromptCoverage(2, "b", "", pc.UNKNOWN),
        ]
        summary = pc.summarize(rows)
        self.assertEqual(summary["coverage_pct"], 100.0)
        self.assertEqual(summary["measurable"], 1)

    def test_all_unknown_yields_no_percentage_rather_than_zero(self):
        summary = pc.summarize([pc.PromptCoverage(1, "a", "", pc.UNKNOWN)])
        self.assertIsNone(summary["coverage_pct"])


class SummaryTests(SimpleTestCase):
    def _rows(self):
        return [
            pc.PromptCoverage(1, "covered one", "", pc.COVERED, best_score=0.8),
            pc.PromptCoverage(2, "weak one", "", pc.WEAK, best_url="https://a.com/x", best_score=0.5),
            pc.PromptCoverage(3, "missing one", "", pc.UNCOVERED, best_score=0.1),
        ]

    def test_counts_each_band(self):
        s = pc.summarize(self._rows())
        self.assertEqual((s["covered"], s["weak"], s["uncovered"]), (1, 1, 1))

    def test_needs_page_is_the_write_queue(self):
        self.assertEqual(pc.summarize(self._rows())["needs_page"], ["missing one"])

    def test_needs_section_names_the_page_to_edit(self):
        needs = pc.summarize(self._rows())["needs_section"]
        self.assertEqual(needs, [{"prompt": "weak one", "url": "https://a.com/x"}])

    def test_empty_input_is_safe(self):
        s = pc.summarize([])
        self.assertEqual(s["total_prompts"], 0)
        self.assertIsNone(s["coverage_pct"])


class FailSoftTests(SimpleTestCase):
    def test_blank_prompts_are_skipped(self):
        rows = _coverage([_chunk("https://a.com", 0.9)], prompts=[_prompt(1, "   ")])
        self.assertEqual(rows, [])

    def test_no_prompts_returns_empty(self):
        self.assertEqual(pc.coverage_for_run(_run(), prompts=[]), [])

    def test_report_never_raises(self):
        with patch.object(pc, "coverage_for_run", side_effect=RuntimeError("db down")):
            report = pc.report_for_run(_run())
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["summary"]["total_prompts"], 0)

    def test_rows_are_serializable(self):
        """The report goes straight into a DRF Response, so it must be JSON-safe."""
        import json

        rows = _coverage([_chunk("https://a.com", 0.9, ["Docs", "GEO"])])
        with patch.object(pc, "coverage_for_run", return_value=rows):
            report = pc.report_for_run(_run())
        json.dumps(report)  # must not raise
        self.assertEqual(report["rows"][0]["best_heading"], "Docs > GEO")
        self.assertEqual(report["summary"]["covered"], 1)


class EndpointTests(TestCase):
    """GET /runs/s/<slug>/prompt-coverage/ and POST .../answer-block/"""

    def setUp(self):
        from apps.analyzer.models import AnalysisRun, PromptTrack
        from apps.organizations.models import Organization

        self.org = Organization.objects.create(name="Acme", owner_email="o@acme.com")
        self.run = AnalysisRun.objects.create(url="https://acme.com", organization=self.org)
        self.track = PromptTrack.objects.create(analysis_run=self.run, prompt_text="what is GEO?")

    def _owner(self):
        """answer-block is billable, so it requires a verified owner."""
        from apps.analyzer.tests.auth_helpers import signed_in

        return signed_in(self.org.owner_email)

    def test_coverage_endpoint_returns_rows_and_summary(self):
        from django.urls import reverse

        with self._owner():
            resp = self.client.get(reverse("analyzer:prompt-coverage", args=[self.run.slug]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("summary", resp.json())
        self.assertIn("rows", resp.json())

    def test_coverage_unknown_slug_is_404(self):
        from django.urls import reverse

        with self._owner():
            resp = self.client.get(reverse("analyzer:prompt-coverage", args=["nope"]))
        self.assertEqual(resp.status_code, 404)

    def test_answer_block_is_a_post_not_a_get(self):
        """It costs money on every call, so it must not be triggerable by a page load."""
        from django.urls import reverse

        url = reverse("analyzer:prompt-answer-block", args=[self.run.slug, self.track.id])
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_answer_block_returns_a_draft(self):
        from unittest.mock import patch

        from django.urls import reverse

        draft = {"prompt": "q", "heading": "h", "answer": "a", "mode": "new_page"}
        with self._owner(), patch(
            "apps.analyzer.services.answer_block.generate_for_prompt", return_value=draft
        ):
            resp = self.client.post(
                reverse("analyzer:prompt-answer-block", args=[self.run.slug, self.track.id])
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["heading"], "h")

    def test_an_enforced_deployment_refuses_an_anonymous_draft(self):
        """Billable, so the slug alone must not trigger it once auth is live."""
        from django.urls import reverse

        url = reverse("analyzer:prompt-answer-block", args=[self.run.slug, self.track.id])
        with self.settings(
            BETTER_AUTH_JWKS_URL="https://auth.example/jwks", REQUIRE_VERIFIED_IDENTITY=True
        ):
            self.assertEqual(self.client.post(url).status_code, 401)

    def test_answer_block_refuses_a_verified_stranger(self):
        from django.urls import reverse

        from apps.analyzer.tests.auth_helpers import signed_in

        url = reverse("analyzer:prompt-answer-block", args=[self.run.slug, self.track.id])
        with signed_in("stranger@example.com"):
            self.assertEqual(self.client.post(url).status_code, 404)

    def test_a_failed_draft_is_reported_not_silently_empty(self):
        from unittest.mock import patch

        from django.urls import reverse

        with self._owner(), patch(
            "apps.analyzer.services.answer_block.generate_for_prompt", return_value=None
        ):
            resp = self.client.post(
                reverse("analyzer:prompt-answer-block", args=[self.run.slug, self.track.id])
            )
        self.assertEqual(resp.status_code, 502)

    def test_a_track_from_another_run_is_not_reachable(self):
        from unittest.mock import patch

        from django.urls import reverse

        from apps.analyzer.models import AnalysisRun, PromptTrack

        other = AnalysisRun.objects.create(url="https://other.com")
        foreign = PromptTrack.objects.create(analysis_run=other, prompt_text="x")
        with self._owner(), patch(
            "apps.analyzer.services.answer_block.generate_for_prompt", return_value={}
        ):
            resp = self.client.post(
                reverse("analyzer:prompt-answer-block", args=[self.run.slug, foreign.id])
            )
        self.assertEqual(resp.status_code, 404)
