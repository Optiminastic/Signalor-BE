"""Epic 8 regression tests: real search signals, deterministic robots.txt, shared helpers.

The core guarantee here is **never award points for a guess**: when the search backend is
unavailable the checks must report *unknown* and score zero, rather than inventing an
answer (which is exactly what the removed LLM-based checks did).
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.analyzer.pipeline import ai_visibility, entity, serper
from apps.analyzer.pipeline.crawl_files import AI_CRAWLERS, build_robots_txt
from apps.analyzer.pipeline.schema_gen import build_jsonld_prompt, ensure_script_wrapped

_SEARCH = "apps.analyzer.pipeline.serper.search"


def _serper_payload(*, panel=False, organic=None):
    data = {"organic": organic or []}
    if panel:
        data["knowledgeGraph"] = {"title": "Acme Inc", "type": "Company"}
    return data


class SerperClientTests(SimpleTestCase):
    def test_unconfigured_returns_none_not_a_guess(self):
        with patch.object(serper, "api_key", return_value=None):
            self.assertIsNone(serper.search("acme"))

    def test_blank_query_returns_none(self):
        with patch.object(serper, "api_key", return_value="k"):
            self.assertIsNone(serper.search("   "))


class BrandSearchSignalsTests(SimpleTestCase):
    def test_unavailable_search_is_unknown_never_guessed(self):
        with patch(_SEARCH, return_value=None):
            panel, mentions = entity._brand_search_signals("Acme", "acme.com")
        self.assertIsNone(panel)
        self.assertIsNone(mentions)

    def test_knowledge_panel_detected_from_knowledge_graph(self):
        with patch(_SEARCH, return_value=_serper_payload(panel=True)):
            panel, _ = entity._brand_search_signals("Acme", "acme.com")
        self.assertTrue(panel)

    def test_no_knowledge_graph_means_no_panel(self):
        with patch(_SEARCH, return_value=_serper_payload(panel=False)):
            panel, _ = entity._brand_search_signals("Acme", "acme.com")
        self.assertFalse(panel)

    def test_own_domain_is_not_a_third_party_mention(self):
        organic = [
            {"link": "https://acme.com/about", "title": "Acme", "snippet": "Acme official"},
            {"link": "https://www.acme.com/blog", "title": "Acme", "snippet": "Acme blog"},
            {"link": "https://techcrunch.com/acme", "title": "Acme raises", "snippet": "Acme news"},
        ]
        with patch(_SEARCH, return_value=_serper_payload(organic=organic)):
            _, mentions = entity._brand_search_signals("Acme", "acme.com")
        self.assertEqual(mentions, 1)  # only techcrunch counts

    def test_result_without_brand_in_text_is_not_counted(self):
        organic = [{"link": "https://example.com/x", "title": "Unrelated", "snippet": "nothing"}]
        with patch(_SEARCH, return_value=_serper_payload(organic=organic)):
            _, mentions = entity._brand_search_signals("Acme", "acme.com")
        self.assertEqual(mentions, 0)


class GooglePresenceTests(SimpleTestCase):
    def test_unknown_when_search_unavailable(self):
        with patch(_SEARCH, return_value=None):
            out = ai_visibility._check_google_presence("Acme", "acme.com")
        self.assertTrue(out["unknown"])
        self.assertFalse(out["found"])
        self.assertEqual(out["signals"], [])

    def test_found_when_own_domain_ranks(self):
        organic = [{"link": "https://acme.com/", "title": "Acme", "snippet": ""}]
        with patch(_SEARCH, return_value=_serper_payload(organic=organic)):
            out = ai_visibility._check_google_presence("Acme", "acme.com")
        self.assertTrue(out["found"])
        self.assertIn("google_search", out["signals"])
        self.assertFalse(out["unknown"])

    def test_knowledge_panel_counts_as_presence(self):
        with patch(_SEARCH, return_value=_serper_payload(panel=True)):
            out = ai_visibility._check_google_presence("Acme", "acme.com")
        self.assertTrue(out["found"])
        self.assertIn("google_knowledge_panel", out["signals"])

    def test_not_found_when_absent(self):
        organic = [{"link": "https://someoneelse.com/", "title": "Other", "snippet": ""}]
        with patch(_SEARCH, return_value=_serper_payload(organic=organic)):
            out = ai_visibility._check_google_presence("Acme", "acme.com")
        self.assertFalse(out["found"])
        self.assertFalse(out["unknown"])

    def test_ai_overview_signal_is_gone(self):
        # It was invented by an LLM; Serper cannot observe it, so it must never appear.
        with patch(_SEARCH, return_value=_serper_payload(panel=True)):
            out = ai_visibility._check_google_presence("Acme", "acme.com")
        self.assertNotIn("google_ai_overview", out["signals"])


class RobotsTxtTests(SimpleTestCase):
    def test_is_deterministic_and_needs_no_llm(self):
        a = build_robots_txt("https://acme.com")
        b = build_robots_txt("https://acme.com")
        self.assertEqual(a, b)

    def test_allows_every_ai_crawler_and_never_disallows(self):
        out = build_robots_txt("https://acme.com/")
        for bot in AI_CRAWLERS:
            self.assertIn(f"User-agent: {bot}", out)
        self.assertNotIn("Disallow", out)  # a hallucinated Disallow could deindex a site

    def test_points_at_the_sitemap(self):
        self.assertIn("Sitemap: https://acme.com/sitemap.xml", build_robots_txt("https://acme.com/"))

    def test_handles_missing_url(self):
        self.assertNotIn("Sitemap:", build_robots_txt(""))


class SchemaGenTests(SimpleTestCase):
    def test_single_prompt_used_by_both_fix_paths(self):
        out = build_jsonld_prompt(brand="Acme", url="https://acme.com", context="ctx")
        self.assertIn("Acme", out)
        self.assertIn("PAGE CONTEXT: ctx", out)
        self.assertIn("Do not invent", out)

    def test_context_is_optional(self):
        out = build_jsonld_prompt(brand="Acme", url="https://acme.com")
        self.assertNotIn("PAGE CONTEXT", out)

    def test_wraps_bare_json_once(self):
        wrapped = ensure_script_wrapped('{"@type": "Organization"}')
        self.assertEqual(wrapped.count("<script"), 1)
        self.assertIn('type="application/ld+json"', wrapped)

    def test_does_not_double_wrap(self):
        already = '<script type="application/ld+json">{"a":1}</script>'
        self.assertEqual(ensure_script_wrapped(already), already)
