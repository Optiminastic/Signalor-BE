"""Regression tests for grounded competitor discovery and visibility checks.

The guarantee under test is the same one ``test_tech_debt`` protects for entity
and AI-visibility checks, extended to the last three places that still answered
from model memory: **never report a company, a mention, or a metric that a
search engine did not actually return.**

These checks used to degrade silently. Competitor discovery scraped DuckDuckGo,
which had started returning HTTP 302 with zero results; the empty candidate list
was swallowed and the LLM invented five companies from training data. The Google
and web-mention checks fell back to asking a model for Knowledge Panel presence
and "up to 15 specific, realistic mentions". Every test below pins one half of
the fix: the search must be real, and an unavailable search must produce nothing
rather than a guess.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.analyzer.pipeline import competitors
from apps.visibility.pipeline import google_check, web_mentions_check

_SEARCH = "core.llm.serper.search"
_CONFIGURED = "core.llm.serper.is_configured"


def _organic(*links):
    return {
        "organic": [
            {"link": link, "title": f"{link} title", "snippet": f"{link} snippet"} for link in links
        ]
    }


class SearchQueryBuildingTests(SimpleTestCase):
    def test_category_queries_come_before_brand_queries(self):
        queries = competitors._build_search_queries(
            "Signalor", "AI search visibility platform", "GEO SaaS", None
        )
        self.assertTrue(queries[0].startswith("best AI search visibility platform"))
        brand_index = next(i for i, q in enumerate(queries) if q.startswith("Signalor"))
        self.assertGreater(brand_index, 0)

    def test_country_is_appended_when_known(self):
        queries = competitors._build_search_queries("Acme", "running shoes", "", "India")
        self.assertTrue(all(q.endswith(" India") for q in queries))

    def test_industry_duplicating_the_category_is_not_searched_twice(self):
        queries = competitors._build_search_queries("Acme", "running shoes retail", "running shoes", None)
        self.assertEqual(len(queries), 3)  # 2 category + 1 brand, no redundant industry query

    def test_falls_back_to_brand_when_no_category_is_known(self):
        queries = competitors._build_search_queries("Acme", "", "", None)
        self.assertEqual(queries, ["Acme competitors"])

    def test_no_brand_and_no_category_yields_no_query(self):
        self.assertEqual(competitors._build_search_queries("", "", "", None), [])


class WebCandidateTests(SimpleTestCase):
    def _discover(self):
        return competitors._discover_web_candidates(
            brand_name="Acme",
            brand_url="https://acme.com",
            product_category="widgets",
            industry="",
            country=None,
        )

    def test_unconfigured_search_yields_no_candidates(self):
        with patch(_CONFIGURED, return_value=False):
            self.assertEqual(self._discover(), [])

    def test_own_domain_is_never_a_candidate(self):
        payload = _organic("https://acme.com/pricing", "https://rival.com")
        with patch(_CONFIGURED, return_value=True), patch(_SEARCH, return_value=payload):
            hosts = [c["url"] for c in self._discover()]
        self.assertNotIn("https://acme.com", hosts)
        self.assertIn("https://rival.com", hosts)

    def test_listicle_and_social_hosts_are_excluded(self):
        payload = _organic(
            "https://medium.com/best-widgets",
            "https://reddit.com/r/widgets",
            "https://github.com/widgets",
            "https://rival.com",
        )
        with patch(_CONFIGURED, return_value=True), patch(_SEARCH, return_value=payload):
            hosts = [c["url"] for c in self._discover()]
        self.assertEqual(hosts, ["https://rival.com"])

    def test_candidates_are_deduplicated_across_queries(self):
        payload = _organic("https://rival.com/a", "https://rival.com/b")
        with patch(_CONFIGURED, return_value=True), patch(_SEARCH, return_value=payload):
            candidates = self._discover()
        self.assertEqual(len(candidates), 1)

    def test_title_and_snippet_are_carried_through_for_the_selector(self):
        with patch(_CONFIGURED, return_value=True), patch(_SEARCH, return_value=_organic("https://rival.com")):
            candidate = self._discover()[0]
        self.assertTrue(candidate["title"])
        self.assertTrue(candidate["snippet"])
        self.assertEqual(candidate["source"], "serper")

    def test_failed_search_is_not_treated_as_zero_competitors(self):
        with patch(_CONFIGURED, return_value=True), patch(_SEARCH, return_value=None):
            self.assertEqual(self._discover(), [])


class HallucinationGateTests(SimpleTestCase):
    def test_model_may_not_introduce_a_domain_the_search_did_not_return(self):
        allowed = {"rival.com"}
        picked = [
            {"name": "Rival", "url": "https://rival.com"},
            {"name": "Invented", "url": "https://geogen.io"},
        ]
        kept = competitors._filter_to_allowed_hosts(picked, allowed)
        self.assertEqual([c["name"] for c in kept], ["Rival"])

    def test_empty_allow_list_rejects_everything(self):
        picked = [{"name": "Invented", "url": "https://nevar.ai"}]
        self.assertEqual(competitors._filter_to_allowed_hosts(picked, set()), [])

    def test_the_gate_is_www_insensitive(self):
        """Search returns www.rival.com; a model routinely writes back rival.com.

        Comparing bare netlocs discarded that as a hallucination, silently losing
        a legitimately discovered competitor.
        """
        allowed = competitors._candidate_hosts([{"url": "https://www.rival.com"}])
        kept = competitors._filter_to_allowed_hosts([{"name": "Rival", "url": "https://rival.com"}], allowed)
        self.assertEqual([c["name"] for c in kept], ["Rival"])

    def test_the_gate_is_www_insensitive_in_the_other_direction(self):
        allowed = competitors._candidate_hosts([{"url": "https://rival.com"}])
        kept = competitors._filter_to_allowed_hosts(
            [{"name": "Rival", "url": "https://www.rival.com"}], allowed
        )
        self.assertEqual([c["name"] for c in kept], ["Rival"])


class HostKeyTests(SimpleTestCase):
    """One definition of "same site" shared by discovery, the gate and the top-up."""

    def test_www_and_bare_hosts_are_the_same_key(self):
        self.assertEqual(
            competitors._host_key("https://www.acme.com"), competitors._host_key("http://acme.com/x")
        )

    def test_an_interior_www_is_not_stripped(self):
        """``replace`` turned mywww.site.com into mysite.com; ``removeprefix`` does not."""
        self.assertEqual(competitors._host_key("https://mywww.site.com"), "mywww.site.com")

    def test_a_subdomain_is_not_collapsed_into_its_parent(self):
        self.assertEqual(competitors._host_key("https://blog.acme.com"), "blog.acme.com")

    def test_junk_yields_an_empty_key(self):
        for value in ("", "   ", None):
            self.assertEqual(competitors._host_key(value), "")

    def test_no_llm_is_called_when_the_search_returns_nothing(self):
        """The core regression: an empty search must short-circuit, not prompt anyway."""
        with (
            patch.object(competitors, "_discover_web_candidates", return_value=[]),
            patch("core.llm.client.ask_llm") as mock_llm,
        ):
            result = competitors._discover_competitors_llm(
                brand_name="Acme",
                brand_url="https://acme.com",
                understanding={"product_category": "widgets", "one_liner": "widgets"},
                site_context="Acme sells widgets",
            )
        self.assertEqual(result, [])
        mock_llm.assert_not_called()


class GoogleCheckTests(SimpleTestCase):
    def _check(self):
        return google_check.check_google("Acme", "https://acme.com")

    def test_unknown_scores_zero_when_no_backend_is_available(self):
        with patch(_CONFIGURED, return_value=False), patch.object(
            google_check, "_check_via_scraper", return_value=None
        ):
            score, details = self._check()
        self.assertEqual(score, 0.0)
        self.assertTrue(details["unknown"])

    def test_knowledge_panel_is_observed_not_estimated(self):
        payload = _organic("https://acme.com/")
        payload["knowledgeGraph"] = {"title": "Acme Inc"}
        with patch(_CONFIGURED, return_value=True), patch(_SEARCH, return_value=payload):
            _, details = self._check()
        self.assertTrue(details["has_knowledge_panel"])
        self.assertEqual(details["method"], "serper")

    def test_absent_knowledge_panel_is_false_not_a_guess(self):
        with patch(_CONFIGURED, return_value=True), patch(_SEARCH, return_value=_organic("https://other.com/")):
            _, details = self._check()
        self.assertFalse(details["has_knowledge_panel"])
        self.assertIsNone(details["brand_rank_position"])

    def test_brand_rank_reflects_real_position(self):
        payload = _organic("https://other.com/", "https://acme.com/")
        with patch(_CONFIGURED, return_value=True), patch(_SEARCH, return_value=payload):
            _, details = self._check()
        self.assertEqual(details["brand_rank_position"], 2)
        self.assertEqual(details["brand_results_count"], 1)


class WebMentionsTests(SimpleTestCase):
    def _check(self):
        return web_mentions_check.check_web_mentions("Acme", "https://acme.com")

    def test_unknown_scores_zero_when_no_backend_is_available(self):
        with patch(_CONFIGURED, return_value=False):
            score, details = self._check()
        self.assertEqual(score, 0.0)
        self.assertTrue(details["unknown"])
        self.assertEqual(details["mentions"], [])

    def test_only_real_third_party_urls_are_reported(self):
        payload = _organic(
            "https://techcrunch.com/acme",
            "https://acme.com/about",
            "https://reddit.com/r/acme",
        )
        with patch(_CONFIGURED, return_value=True), patch(_SEARCH, return_value=payload):
            _, details = self._check()
        domains = {m["domain"] for m in details["mentions"]}
        self.assertEqual(domains, {"techcrunch.com"})

    def test_failed_search_reports_unknown_rather_than_zero_mentions(self):
        with patch(_CONFIGURED, return_value=True), patch(_SEARCH, return_value=None):
            score, details = self._check()
        self.assertEqual(score, 0.0)
        self.assertTrue(details["unknown"])
