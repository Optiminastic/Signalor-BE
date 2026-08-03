"""Tasks must be grounded in everything the run measured, not a fraction of it.

`site_findings` read four sources — crawl, pillar checks, SiteOne, GA4/GSC — while
the run had already recorded which engines answered which prompt and who they
cited instead, whether any AI crawler ever fetched a page, and which tracked
prompts have no answering page. Reasoning without those is why the generic rule
engine still looked competitive.

Two contracts carry this:

1. **Absent is stated, never zero.** A collector returns None when its source has
   nothing, and the prompt renders "not measured". "No telemetry" must never be
   read as "no crawler visited".
2. **Every collector is independently fail-soft.** One broken source costs one
   section of grounding, never the discovery pass.
"""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.analyzer.models import (
    AnalysisRun,
    Competitor,
    PromptCitation,
    PromptResult,
    PromptTrack,
)
from apps.analyzer.pipeline import site_findings as sf
from apps.analyzer.services import task_signals as ts
from apps.analyzer.services.attribution import attribution_for
from apps.organizations.models import Organization


class PromptCitationCollectorTests(TestCase):
    """The strongest signal: what engines answered and who they cited instead."""

    def setUp(self):
        self.org = Organization.objects.create(name="Acme", owner_email="o@acme.com")
        self.run = AnalysisRun.objects.create(
            url="https://acme.com", organization=self.org, brand_name="Acme"
        )

    def _track(self, text, *, mentioned=False, cites=()):
        track = PromptTrack.objects.create(analysis_run=self.run, prompt_text=text)
        result = PromptResult.objects.create(
            prompt_track=track, engine="chatgpt", brand_mentioned=mentioned
        )
        for domain in cites:
            PromptCitation.objects.create(
                prompt_result=result, url=f"https://{domain}/x", domain=domain
            )
        return track

    def test_a_lost_prompt_reports_who_was_cited_instead(self):
        self._track("best geo tools for ecommerce", cites=["hubspot.com", "semrush.com"])
        data = ts.collect_prompt_citations(self.run)
        self.assertEqual(data["lost_count"], 1)
        row = data["prompts"][0]
        self.assertIn("hubspot.com", row["cited_instead"])
        self.assertIn("semrush.com", row["cited_instead"])

    def test_a_won_prompt_carries_no_cited_instead(self):
        self._track("q", mentioned=True, cites=["hubspot.com"])
        row = ts.collect_prompt_citations(self.run)["prompts"][0]
        self.assertNotIn("cited_instead", row)

    def test_top_cited_instead_is_ranked(self):
        self._track("q1", cites=["hubspot.com", "semrush.com"])
        self._track("q2", cites=["hubspot.com"])
        top = ts.collect_prompt_citations(self.run)["top_cited_instead"]
        self.assertEqual(top[0][0], "hubspot.com")

    def test_a_prompt_that_never_fired_is_excluded(self):
        """No results means unknown, not lost — the same rule citation_gaps uses."""
        PromptTrack.objects.create(analysis_run=self.run, prompt_text="never fired")
        self.assertIsNone(ts.collect_prompt_citations(self.run))

    def test_the_brands_own_citation_is_not_a_competitor(self):
        track = self._track("q", cites=["acme.com"])
        PromptCitation.objects.filter(prompt_result__prompt_track=track).update(is_brand=True)
        row = ts.collect_prompt_citations(self.run)["prompts"][0]
        self.assertEqual(row["cited_instead"], [])

    def test_www_is_normalised(self):
        self._track("q", cites=["www.hubspot.com"])
        row = ts.collect_prompt_citations(self.run)["prompts"][0]
        self.assertEqual(row["cited_instead"], ["hubspot.com"])

    def test_no_prompts_at_all_is_none_not_empty(self):
        self.assertIsNone(ts.collect_prompt_citations(self.run))


class CompetitorCollectorTests(TestCase):
    def setUp(self):
        self.run = AnalysisRun.objects.create(url="https://acme.com", brand_name="Acme")

    def test_competitors_are_ranked_by_score(self):
        Competitor.objects.create(analysis_run=self.run, name="Low", url="https://l.com", composite_score=30)
        Competitor.objects.create(analysis_run=self.run, name="High", url="https://h.com", composite_score=80)
        rows = ts.collect_competitors(self.run)
        self.assertEqual(rows[0]["name"], "High")

    def test_no_competitors_is_none(self):
        self.assertIsNone(ts.collect_competitors(self.run))


class FailSoftTests(TestCase):
    """One broken source must cost one section, never the whole set."""

    def setUp(self):
        self.run = AnalysisRun.objects.create(url="https://acme.com", brand_name="Acme")

    def test_a_raising_collector_returns_none(self):
        with patch.object(ts, "collect_prompt_citations", wraps=ts.collect_prompt_citations):
            with patch("apps.analyzer.models.PromptTrack.objects") as objects:
                objects.filter.side_effect = RuntimeError("db down")
                self.assertIsNone(ts.collect_prompt_citations(self.run))

    def test_collect_all_returns_every_key_even_when_empty(self):
        """Keys must exist with None so the prompt renders 'not measured'."""
        data = ts.collect_all(self.run)
        expected = {
            "prompt_citations",
            "competitors",
            "crawler_telemetry",
            "prompt_coverage",
            "citation_gaps",
            "domain_authority",
            "brand_profile",
        }
        self.assertEqual(set(data), expected)

    def test_collect_all_survives_a_source_that_explodes(self):
        with patch.object(ts, "collect_competitors", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                ts.collect_competitors(self.run)  # the patch replaces the guard
        # Unpatched, the real decorated collector swallows and returns None.
        self.assertIsNone(ts.collect_competitors(self.run))


class AbsentIsNotZeroTests(SimpleTestCase):
    """The rule that keeps findings honest when a source is missing."""

    def test_missing_crawler_telemetry_says_unknown_not_never_visited(self):
        block = sf._crawler_block(None)
        self.assertIn("UNKNOWN", block)
        self.assertIn("do not claim", block.lower())

    def test_missing_prompt_tracking_says_not_measured(self):
        self.assertIn("not measured", sf._citations_block(None))

    def test_missing_coverage_says_not_measured(self):
        self.assertIn("not measured", sf._coverage_block(None))

    def test_missing_authority_says_unknown(self):
        self.assertIn("UNKNOWN", sf._authority_block(None))

    def test_no_competitors_is_stated_plainly(self):
        self.assertIn("none discovered", sf._competitors_block(None))


class BlockRenderingTests(SimpleTestCase):
    def test_the_citations_block_names_prompt_engines_and_competitor(self):
        """A finding can only name them if the block carries them."""
        block = sf._citations_block(
            {
                "lost_count": 1,
                "tracked_count": 3,
                "top_cited_instead": [("hubspot.com", 2)],
                "prompts": [
                    {
                        "prompt": "best geo tools for ecommerce",
                        "engines_mentioning": 0,
                        "engines_asked": 7,
                        "cited_instead": ["hubspot.com"],
                    }
                ],
            }
        )
        self.assertIn("best geo tools for ecommerce", block)
        self.assertIn("0/7", block)
        self.assertIn("hubspot.com", block)
        self.assertIn("LOST", block)

    def test_the_crawler_block_names_blocked_engines_and_missed_pages(self):
        block = sf._crawler_block(
            {
                "blocked_engines": ["ChatGPT"],
                "engines": [{"engine": "ChatGPT", "status": "blocked", "hits": 0}],
                "uncrawled_pages": ["https://acme.com/pricing"],
            }
        )
        self.assertIn("BLOCKED", block)
        self.assertIn("ChatGPT", block)
        self.assertIn("/pricing", block)

    def test_coverage_separates_needs_page_from_needs_section(self):
        """Two different tasks; conflating them produces vague advice."""
        block = sf._coverage_block(
            {
                "covered": 2,
                "measurable": 5,
                "needs_page": ["how do I track citations"],
                "needs_section": [{"prompt": "pricing", "url": "https://acme.com/p"}],
            }
        )
        self.assertIn("NO page answers", block)
        self.assertIn("answers weakly", block)


class TemplateContractTests(SimpleTestCase):
    """StrictUndefined means a missing block breaks the render, so pin them."""

    def _render(self, **over):
        from apps.analyzer.prompts import render

        args = {
            "brand": "B", "url": "u", "pages_block": "P", "already_found": "A",
            "analyzer_block": "AN", "siteone_block": "S", "analytics_block": "AA",
            "ai_visibility_block": "V", "citations_block": "C", "competitors_block": "CO",
            "crawler_block": "CR", "coverage_block": "CV", "gaps_block": "G",
            "authority_block": "AU", "brand_profile_block": "BP", "count": 6,
        }
        args.update(over)
        return render("site_findings", **args)

    def test_every_new_block_reaches_the_prompt(self):
        out = self._render()
        for marker in ("C", "CO", "CR", "CV", "G", "AU", "BP"):
            self.assertIn(marker, out)

    def test_it_tells_the_model_a_lost_prompt_is_the_best_finding(self):
        out = self._render()
        self.assertIn("LOST PROMPT", out)
        self.assertIn("name who was cited", out)

    def test_it_forbids_conflating_new_page_with_improve_page(self):
        self.assertIn("do not conflate them", self._render())

    def test_it_ranks_crawlability_above_content(self):
        self.assertIn("outranks any content finding", self._render())

    def test_no_placeholder_survives(self):
        out = self._render()
        for token in ("{{", "}}", "{%"):
            self.assertNotIn(token, out)


class AttributionTests(SimpleTestCase):
    """Every task must say which signal completing it moves."""

    def test_pillar_maps_to_a_user_facing_signal(self):
        self.assertEqual(attribution_for("eeat")["signal"], "E-E-A-T")
        self.assertEqual(attribution_for("schema")["signal"], "Schema")
        self.assertEqual(attribution_for("technical")["signal"], "Technical")

    def test_geo_findings_beat_the_pillar(self):
        # A geo signal traces to a measured prompt, so it gets the specific
        # attribution rather than its pillar's generic one.
        out = attribution_for("entity", "geo_citation_gap")
        self.assertEqual(out["signal"], "Off-site")
        self.assertIn("AI engines already cite", out["effect"])

    def test_a_lost_prompt_task_names_the_prompt_it_targets(self):
        """"AI visibility" is a category; the prompt is the actual reason."""
        out = attribution_for(
            "ai_visibility",
            "geo_prompt_lost",
            {"prompt": "What are the best alternatives to Signalor?"},
        )
        self.assertEqual(out["signal"], "Prompt")
        self.assertIn("What are the best alternatives to Signalor?", out["effect"])

    def test_a_long_prompt_is_truncated_rather_than_wrapping_the_row(self):
        out = attribution_for("ai_visibility", "geo_prompt_lost", {"prompt": "q" * 300})
        self.assertLess(len(out["effect"]), 140)
        self.assertIn("\u2026", out["effect"])

    def test_falls_back_to_the_geo_label_when_evidence_names_no_prompt(self):
        """Older rows predate prompt-carrying evidence and must still read sensibly."""
        for evidence in (None, {}, {"prompt": "   "}):
            out = attribution_for("ai_visibility", "geo_prompt_lost", evidence)
            self.assertEqual(out["signal"], "AI visibility")
            self.assertTrue(out["effect"])

    def test_unknown_pillar_still_returns_something_usable(self):
        out = attribution_for("", "")
        self.assertTrue(out["signal"])
        self.assertTrue(out["effect"])

    def test_never_promises_a_score_number(self):
        for pillar in ("content", "schema", "eeat", "technical", "entity", "ai_visibility"):
            effect = attribution_for(pillar)["effect"]
            self.assertNotIn("%", effect)
            self.assertNotIn("+", effect)
