"""Tests for the open-ended site audit (pipeline/site_findings.py).

The recommendation engine's 83 rules can only report problems someone wrote a
checker for, which is why every customer's task list read like the same SEO
checklist. This module discovers issues by reading the site instead.

Discovery is also the easiest place in the pipeline to start hallucinating: a
model asked to "find problems" will invent them. So the contract under test is
the evidence gate - every finding must quote real page text verbatim, and
anything that cannot be located in the crawl is dropped rather than shown.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.analyzer.pipeline import site_findings
from apps.analyzer.pipeline.schemas import SiteFinding

_ASK_LIST = "core.llm.structured.ask_structured_list"

_HOME_TEXT = (
    "Signalor is a Generative Engine Optimization platform. "
    "We help brands understand how ChatGPT and Perplexity describe them. "
    "Pricing starts at nothing you can see on this page."
)
_GUIDE_TEXT = (
    "How to improve AI visibility. This guide walks through the steps teams take "
    "to get cited by answer engines, with no author byline anywhere on it."
)


def _crawl(url, text, ok=True):
    return SimpleNamespace(url=url, text=text, ok=ok)


def _crawls():
    return [
        _crawl("https://acme.com", _HOME_TEXT),
        _crawl("https://acme.com/guide", _GUIDE_TEXT),
    ]


def _finding(**over):
    base = dict(
        title="Guide page has no author byline",
        issue="The guide is structured unlike the blog posts and names no author.",
        evidence="with no author byline anywhere on it",
        fix="Add a byline naming the author at the top of the guide.",
        url="https://acme.com/guide",
        pillar="eeat",
        priority="high",
    )
    base.update(over)
    return SiteFinding(**base)


def _discover(findings, **kwargs):
    with patch(_ASK_LIST, return_value=findings):
        return site_findings.discover_site_findings(
            _crawls(), brand="Acme", homepage_url="https://acme.com", **kwargs
        )


class EvidenceGateTests(SimpleTestCase):
    """The gate that separates analysis from invention."""

    def test_finding_quoting_real_page_text_survives(self):
        out = _discover([_finding()])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Guide page has no author byline")

    def test_finding_with_invented_evidence_is_dropped(self):
        out = _discover([_finding(evidence="Our team of 40 dentists has served Manchester since 1998")])
        self.assertEqual(out, [])

    def test_paraphrased_evidence_is_dropped(self):
        """Close-but-not-quoted is still not grounded."""
        out = _discover([_finding(evidence="the guide does not have any author byline on it")])
        self.assertEqual(out, [])

    def test_whitespace_differences_are_tolerated(self):
        """Models reflow whitespace when copying; that is not fabrication."""
        out = _discover([_finding(evidence="with no   author byline\nanywhere on it")])
        self.assertEqual(len(out), 1)

    def test_case_differences_are_tolerated(self):
        out = _discover([_finding(evidence="WITH NO AUTHOR BYLINE ANYWHERE ON IT")])
        self.assertEqual(len(out), 1)

    def test_trivially_short_evidence_is_rejected(self):
        """A two-word quote matches by accident and grounds nothing."""
        out = _discover([_finding(evidence="guide")])
        self.assertEqual(out, [])

    def test_evidence_from_a_different_crawled_page_still_counts(self):
        """Right quote, wrong URL is a citation slip, not a fabrication."""
        out = _discover([_finding(url="https://acme.com", evidence="Pricing starts at nothing")])
        self.assertEqual(len(out), 1)

    def test_grounded_and_ungrounded_are_separated(self):
        out = _discover([_finding(), _finding(title="Invented", evidence="a claim never made")])
        self.assertEqual([r["title"] for r in out], ["Guide page has no author byline"])


class RecommendationShapeTests(SimpleTestCase):
    def test_finding_code_is_namespaced_so_it_cannot_collide_with_a_rule(self):
        code = _discover([_finding()])[0]["finding_code"]
        self.assertTrue(code.startswith("site:"))

    def test_code_fits_the_model_column(self):
        long_title = "A finding with an extremely long title " * 6
        code = _discover([_finding(title=long_title)])[0]["finding_code"]
        self.assertLessEqual(len(code), 80)  # Recommendation.finding_code max_length

    def test_emits_only_real_model_fields(self):
        """recs are splatted into Recommendation(**rec), so a stray key would 500."""
        from apps.analyzer.models import Recommendation

        allowed = {f.name for f in Recommendation._meta.get_fields()}
        self.assertTrue(set(_discover([_finding()])[0]).issubset(allowed))

    def test_marked_as_discovered_not_analyzer(self):
        self.assertEqual(_discover([_finding()])[0]["source"], "ai_insight")

    def test_invalid_pillar_and_priority_fall_back(self):
        rec = _discover([_finding(pillar="vibes", priority="apocalyptic")])[0]
        self.assertEqual(rec["pillar"], "content")
        self.assertEqual(rec["priority"], "medium")

    def test_carries_its_own_generated_content(self):
        """So the enrichment pass has nothing to add and skips it."""
        rec = _discover([_finding()])[0]
        self.assertEqual(rec["generated_content"]["type"], "guidance")
        self.assertEqual(rec["generated_content"]["source"], "site_findings")

    def test_duplicate_titles_are_collapsed(self):
        out = _discover([_finding(), _finding()])
        self.assertEqual(len(out), 1)


class FailSoftTests(SimpleTestCase):
    def test_no_crawled_text_returns_nothing(self):
        with patch(_ASK_LIST, side_effect=AssertionError("should not be called")):
            out = site_findings.discover_site_findings(
                [_crawl("https://acme.com", "", ok=False)], brand="Acme", homepage_url="https://acme.com"
            )
        self.assertEqual(out, [])

    def test_model_returning_nothing_is_not_an_error(self):
        self.assertEqual(_discover(None), [])

    def test_failed_crawls_are_excluded_from_the_corpus(self):
        """Evidence must not be verifiable against a page that failed to load."""
        crawls = [_crawl("https://acme.com", _HOME_TEXT), _crawl("https://acme.com/x", "secret text", ok=False)]
        with patch(_ASK_LIST, return_value=[_finding(evidence="secret text that never loaded")]):
            out = site_findings.discover_site_findings(
                crawls, brand="Acme", homepage_url="https://acme.com"
            )
        self.assertEqual(out, [])


class SignalBlockTests(SimpleTestCase):
    """Absent data sources must read as absent, never as zero."""

    def test_missing_analytics_is_stated_not_omitted(self):
        block = site_findings._analytics_block(None)
        self.assertIn("not connected", block)

    def test_disconnected_gsc_warns_against_inferring(self):
        block = site_findings._analytics_block({"flags": {"has_ga": False, "has_gsc": False}})
        self.assertIn("do not infer", block.lower())

    def test_connected_gsc_surfaces_real_queries(self):
        signals = {
            "flags": {"has_ga": False, "has_gsc": True},
            "gsc": {
                "clicks": 12,
                "impressions": 900,
                "position": 18.4,
                "top_queries": [{"query": "ai visibility tool", "clicks": 2, "impressions": 400}],
            },
        }
        block = site_findings._analytics_block(signals)
        self.assertIn("ai visibility tool", block)
        self.assertIn("900", block)

    def test_missing_siteone_is_stated(self):
        self.assertIn("not available", site_findings._siteone_block(None))

    def test_siteone_deductions_are_surfaced(self):
        payload = {
            "overall_score": 71,
            "categories": [{"name": "Performance", "score": 60, "deductions": [{"reason": "Slow TTFB"}]}],
        }
        block = site_findings._siteone_block(payload)
        self.assertIn("Slow TTFB", block)

    def test_analyzer_checks_are_surfaced(self):
        block = site_findings._pillar_block(
            {"content": {"score": 42.0, "findings": ["low_word_count"], "checks": {"word_count": 180}}}
        )
        self.assertIn("low_word_count", block)
        self.assertIn("180", block)


class ExcerptTruncationTests(SimpleTestCase):
    """Our own excerpt cut must never be reported as a defect on the site.

    Regression: a live run reported "the Explorer page cuts off mid-sentence" as
    a critical finding. The page was fine; the excerpt was ours.
    """

    def test_truncated_page_is_labelled_as_our_cut(self):
        long_text = "word " * (site_findings.PAGE_EXCERPT_CHARS)
        block = site_findings._build_pages_block([_crawl("https://acme.com", long_text)])
        self.assertIn("EXCERPT ENDS HERE", block)
        self.assertIn("page itself is NOT truncated", block)

    def test_short_page_gets_no_truncation_marker(self):
        block = site_findings._build_pages_block([_crawl("https://acme.com", "short page")])
        self.assertNotIn("EXCERPT ENDS HERE", block)


class AttributionTests(SimpleTestCase):
    """Every task must be able to say what it improves and what it costs.

    Discovered findings shipped with an empty `why` and zero effort estimates,
    so the Tasks table had nothing to render for them next to rule-based tasks.
    """

    def test_finding_explains_what_it_improves(self):
        rec = _discover([_finding(pillar="eeat")])[0]
        self.assertTrue(rec["why"])
        self.assertIn("trust", rec["why"].lower())

    def test_rationale_matches_the_pillar(self):
        rec = _discover([_finding(pillar="technical")])[0]
        self.assertEqual(rec["why"], site_findings.PILLAR_RATIONALE["technical"])

    def test_effort_is_populated_so_tasks_are_plannable(self):
        rec = _discover([_finding(priority="critical")])[0]
        self.assertEqual(rec["difficulty"], "medium")
        self.assertGreater(rec["estimated_minutes"], 0)
        self.assertGreater(rec["xp_reward"], 0)

    def test_every_valid_pillar_has_a_rationale(self):
        for pillar in site_findings.VALID_PILLARS:
            self.assertIn(pillar, site_findings.PILLAR_RATIONALE)

    def test_every_valid_priority_has_an_effort_estimate(self):
        for priority in site_findings.VALID_PRIORITIES:
            self.assertIn(priority, site_findings.PRIORITY_EFFORT)


class PageOrderingTests(SimpleTestCase):
    """The pages the model reads are chosen, not whatever crawled first."""

    class _Crawl:
        def __init__(self, url, text, ok=True):
            self.url = url
            self.text = text
            self.ok = ok

    def _crawls(self):
        return [
            self._Crawl("https://acme.com/", "home " * 40),
            self._Crawl("https://acme.com/tiny", "tiny " * 20),
            self._Crawl("https://acme.com/pricing", "pricing " * 30),
            self._Crawl("https://acme.com/long", "long " * 200),
        ]

    def test_homepage_always_leads(self):
        ordered = site_findings._rank_crawls(self._crawls(), None)
        self.assertEqual(ordered[0].url, "https://acme.com/")

    def test_pages_with_real_traffic_outrank_longer_ones(self):
        signals = {"ga": {"top_pages": [{"path": "/pricing"}]}}
        ordered = site_findings._rank_crawls(self._crawls(), signals)
        # /pricing has traffic; /long is longer but nobody reads it.
        self.assertEqual(ordered[1].url, "https://acme.com/pricing")

    def test_gsc_page_key_is_read_too(self):
        # GSC rows use "page" where GA4 uses "path".
        signals = {"gsc": {"top_pages": [{"page": "/tiny"}]}}
        ordered = site_findings._rank_crawls(self._crawls(), signals)
        self.assertEqual(ordered[1].url, "https://acme.com/tiny")

    def test_without_analytics_it_falls_back_to_content_length(self):
        ordered = site_findings._rank_crawls(self._crawls(), None)
        self.assertEqual(ordered[1].url, "https://acme.com/long")

    def test_ordering_never_drops_a_page(self):
        crawls = self._crawls()
        self.assertEqual(len(site_findings._rank_crawls(crawls, None)), len(crawls))
