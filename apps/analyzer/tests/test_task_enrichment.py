"""Tests for LLM task enrichment (services/task_enrichment.py).

The LLM + prompt-registry boundaries are mocked, so these run without network or
Jinja2 and assert the orchestration: dispatch, top-N cap, fail-soft fallback, and
the content-hash cache skip.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.analyzer.pipeline.schemas import (
    CitationItem,
    CitationSuggestions,
    FaqDraft,
    FaqPair,
    ParagraphRewrite,
)
from apps.analyzer.services import task_enrichment
from apps.analyzer.services.task_enrichment import enrich_recommendations

_ASK = "apps.analyzer.pipeline.structured.ask_structured"


def _run():
    return SimpleNamespace(url="https://brand.com", brand_name="Brand", content_hash="abc123")


def _rec(code, impact=5.0, **extra):
    return {"finding_code": code, "impact_points": impact, "generated_content": {}, **extra}


class DispatchTests(SimpleTestCase):
    def setUp(self):
        # Stub prompt rendering, page fetch, and brand corpus so no I/O happens.
        self.p_render = patch.object(task_enrichment, "_render", return_value="PROMPT")
        self.p_page = patch.object(task_enrichment, "_page_content", return_value="<p>content</p>")
        self.p_know = patch.object(task_enrichment, "_brand_knowledge", return_value="")
        self.p_render.start(); self.p_page.start(); self.p_know.start()
        self.addCleanup(self.p_render.stop)
        self.addCleanup(self.p_page.stop)
        self.addCleanup(self.p_know.stop)

    def test_faq_finding_gets_faq_draft(self):
        draft = FaqDraft(pairs=[FaqPair(question="Q?", answer="A.")])
        recs = [_rec("no_faq_section")]
        with patch(_ASK, return_value=draft):
            enrich_recommendations(_run(), recs)
        gc = recs[0]["generated_content"]
        self.assertEqual(gc["type"], "faq")
        self.assertEqual(gc["data"]["pairs"][0]["question"], "Q?")
        self.assertEqual(gc["content_hash"], task_enrichment._content_hash("abc123"))

    def test_citation_finding_gets_citation_draft(self):
        draft = CitationSuggestions(items=[CitationItem(claim="c", source="s", sentence="x")])
        recs = [_rec("no_citations")]
        with patch(_ASK, return_value=draft):
            enrich_recommendations(_run(), recs)
        self.assertEqual(recs[0]["generated_content"]["type"], "citations")

    def test_rewrite_finding_gets_rewrite_draft(self):
        draft = ParagraphRewrite(original="old", rewritten="new")
        recs = [_rec("poor_paragraph_structure")]
        with patch(_ASK, return_value=draft):
            enrich_recommendations(_run(), recs)
        self.assertEqual(recs[0]["generated_content"]["data"]["rewritten"], "new")

    def test_unenrichable_finding_is_left_untouched(self):
        recs = [_rec("no_https")]
        with patch(_ASK, side_effect=AssertionError("should not be called")):
            enrich_recommendations(_run(), recs)
        self.assertEqual(recs[0]["generated_content"], {})

    def test_llm_failure_is_fail_soft(self):
        recs = [_rec("no_faq_section")]
        with patch(_ASK, return_value=None):  # LLM refused / empty
            enrich_recommendations(_run(), recs)
        self.assertEqual(recs[0]["generated_content"], {})  # static action stands

    def test_top_n_cap_enriches_only_highest_impact(self):
        recs = [
            _rec("no_faq_section", impact=9.0),
            _rec("no_faqpage_schema", impact=1.0),
        ]
        draft = FaqDraft(pairs=[FaqPair(question="Q?", answer="A.")])
        with patch(_ASK, return_value=draft):
            enrich_recommendations(_run(), recs, top_n=1)
        # Only the highest-impact FAQ task was enriched.
        by_code = {r["finding_code"]: r for r in recs}
        self.assertTrue(by_code["no_faq_section"]["generated_content"])
        self.assertEqual(by_code["no_faqpage_schema"]["generated_content"], {})

    def test_content_hash_skip_avoids_regeneration(self):
        run = _run()
        h = task_enrichment._content_hash(run.content_hash)
        recs = [_rec("no_faq_section")]
        recs[0]["generated_content"] = {"type": "faq", "data": {"pairs": [{}]}, "content_hash": h}
        with patch(_ASK, side_effect=AssertionError("should not regenerate")):
            enrich_recommendations(run, recs)  # unchanged page -> skip

    def test_no_page_content_skips_all(self):
        recs = [_rec("no_faq_section")]
        with patch.object(task_enrichment, "_page_content", return_value=""), \
             patch(_ASK, side_effect=AssertionError("should not be called")):
            enrich_recommendations(_run(), recs)
        self.assertEqual(recs[0]["generated_content"], {})
