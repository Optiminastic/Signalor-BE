"""The PDF export's Buyer Prompts section.

Two contracts matter here.

The export must carry the run's ``PromptTrack`` rows - the brand-specific
questions a buyer actually asks. It previously carried only ``ai_probes``, the
generic ``INDUSTRY_PROBES`` templates, so the prompts the product is built
around never reached the report a customer is sent.

More importantly: a prompt no engine answered must never be presented as an
absence. An engine that errors persists an empty ``response_text`` with
``brand_mentioned=False``, which is byte-identical to a genuine "the brand was
not mentioned". Reporting a failed measurement as a finding overstates it, and
this report goes to prospects.
"""

from django.template.loader import render_to_string
from django.test import TestCase

from apps.analyzer.models import AnalysisRun, PromptCitation, PromptResult, PromptTrack
from apps.analyzer.views.runs import _prompt_benchmark_rows


class PromptBenchmarkRowsTests(TestCase):
    def setUp(self):
        self.run = AnalysisRun.objects.create(
            url="https://outrun.example",
            email="buyer@example.com",
            status=AnalysisRun.Status.COMPLETE,
        )

    def _track(self, text, score=0.0):
        return PromptTrack.objects.create(analysis_run=self.run, prompt_text=text, score=score)

    def test_answered_prompt_reports_mentions_per_engine(self):
        track = self._track("best policy administration platforms")
        PromptResult.objects.create(
            prompt_track=track,
            engine=PromptResult.Engine.CHATGPT,
            response_text="Outrun is a strong option.",
            brand_mentioned=True,
        )
        PromptResult.objects.create(
            prompt_track=track,
            engine=PromptResult.Engine.CLAUDE,
            response_text="Consider Guidewire.",
            brand_mentioned=False,
        )

        (row,) = _prompt_benchmark_rows(self.run)

        self.assertTrue(row["measured"])
        self.assertEqual(row["mentions"], 1)
        self.assertEqual(len(row["engines"]), 2)
        self.assertTrue(all(engine["answered"] for engine in row["engines"]))

    def test_prompt_no_engine_answered_is_not_measured(self):
        """The contract that protects the report from overstating a finding."""
        track = self._track("schemes broker software")
        for engine in (PromptResult.Engine.CHATGPT, PromptResult.Engine.GEMINI):
            PromptResult.objects.create(
                prompt_track=track,
                engine=engine,
                response_text="",  # what an errored engine persists
                brand_mentioned=False,
            )

        (row,) = _prompt_benchmark_rows(self.run)

        self.assertFalse(row["measured"])
        self.assertEqual(row["mentions"], 0)
        self.assertFalse(any(engine["answered"] for engine in row["engines"]))

    def test_partially_answered_prompt_still_counts_as_measured(self):
        track = self._track("MGA platform comparison")
        PromptResult.objects.create(
            prompt_track=track,
            engine=PromptResult.Engine.CHATGPT,
            response_text="",
            brand_mentioned=False,
        )
        PromptResult.objects.create(
            prompt_track=track,
            engine=PromptResult.Engine.PERPLEXITY,
            response_text="Guidewire and Duck Creek lead here.",
            brand_mentioned=False,
        )

        (row,) = _prompt_benchmark_rows(self.run)

        self.assertTrue(row["measured"])
        self.assertEqual(row["mentions"], 0)

    def test_competitor_domains_are_collected_and_brand_excluded(self):
        track = self._track("who should I shortlist")
        result = PromptResult.objects.create(
            prompt_track=track,
            engine=PromptResult.Engine.CHATGPT,
            response_text="Guidewire leads.",
            brand_mentioned=False,
        )
        PromptCitation.objects.create(
            prompt_result=result, url="https://guidewire.com/a", domain="guidewire.com", position=1
        )
        PromptCitation.objects.create(
            prompt_result=result, url="https://outrun.example/x", domain="outrun.example",
            is_brand=True, position=2,
        )
        # Duplicate domain must collapse rather than repeat in the report.
        PromptCitation.objects.create(
            prompt_result=result, url="https://guidewire.com/b", domain="guidewire.com", position=3
        )

        (row,) = _prompt_benchmark_rows(self.run)

        self.assertEqual(row["cited_domains"], ["guidewire.com"])

    def test_soft_deleted_prompts_are_excluded(self):
        from django.utils import timezone

        self._track("live prompt")
        PromptTrack.objects.create(
            analysis_run=self.run, prompt_text="removed prompt", deleted_at=timezone.now()
        )

        rows = _prompt_benchmark_rows(self.run)

        self.assertEqual([row["prompt"] for row in rows], ["live prompt"])

    def test_run_without_prompts_yields_no_rows(self):
        self.assertEqual(_prompt_benchmark_rows(self.run), [])


class ReportTemplateTests(TestCase):
    """The section renders — a template syntax error would only surface in prod."""

    def _render(self, rows):
        return render_to_string(
            "analyzer/report.html",
            {
                "run": AnalysisRun(url="https://outrun.example"),
                "main_page": None,
                "main_page_pillars": [],
                "recommendations": [],
                "competitors": [],
                "prompt_tracks": rows,
                "ai_probes": [],
            },
        )

    def test_answered_prompt_renders_prompt_and_engines(self):
        html = self._render(
            [
                {
                    "prompt": "best policy administration platforms",
                    "intent": "Information",
                    "prompt_type": "Organic",
                    "engines": [{"label": "ChatGPT", "mentioned": True, "answered": True}],
                    "mentions": 1,
                    "measured": True,
                    "cited_domains": ["guidewire.com"],
                }
            ]
        )

        self.assertIn("Buyer Prompts", html)
        self.assertIn("best policy administration platforms", html)
        self.assertIn("ChatGPT", html)
        self.assertIn("guidewire.com", html)
        self.assertNotIn("Not measured", html)

    def test_unmeasured_prompt_says_so_instead_of_claiming_absence(self):
        html = self._render(
            [
                {
                    "prompt": "schemes broker software",
                    "intent": "Information",
                    "prompt_type": "Organic",
                    "engines": [{"label": "ChatGPT", "mentioned": False, "answered": False}],
                    "mentions": 0,
                    "measured": False,
                    "cited_domains": [],
                }
            ]
        )

        self.assertIn("Not measured", html)
        self.assertNotIn("Not mentioned in any engine", html)
