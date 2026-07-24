"""Tests for the Task Satisfaction Gate (pipeline/satisfaction.py)."""

from django.test import SimpleTestCase

from apps.analyzer.pipeline.satisfaction import (
    SATISFACTION_VERIFIERS,
    PageSignals,
    filter_satisfied,
)

_FAQ_HTML = """
<html><head></head><body>
  <h2>Frequently Asked Questions</h2>
  <details><summary>How does it work?</summary><p>Like this.</p></details>
</body></html>
"""

_SCHEMA_HTML = """
<html><head>
  <script type="application/ld+json">
  {"@context":"https://schema.org","@type":"FAQPage","mainEntity":[]}
  </script>
</head><body><p>hi</p></body></html>
"""

_BARE_HTML = "<html><head></head><body><p>just text</p></body></html>"


def _sig(url: str, html: str) -> PageSignals:
    ps = PageSignals.from_html(url, html)
    assert ps is not None
    return ps


class VerifierTests(SimpleTestCase):
    def test_faq_satisfied_by_visible_heading_or_details(self):
        self.assertTrue(SATISFACTION_VERIFIERS["no_faq_section"](_sig("u", _FAQ_HTML)))

    def test_faq_not_satisfied_on_bare_page(self):
        self.assertFalse(SATISFACTION_VERIFIERS["no_faq_section"](_sig("u", _BARE_HTML)))

    def test_faqpage_schema_detected_from_jsonld(self):
        self.assertTrue(SATISFACTION_VERIFIERS["no_faqpage_schema"](_sig("u", _SCHEMA_HTML)))
        self.assertFalse(SATISFACTION_VERIFIERS["no_faqpage_schema"](_sig("u", _BARE_HTML)))

    def test_publish_date_from_time_tag(self):
        html = '<html><body><time datetime="2025-01-15">Jan 15</time></body></html>'
        self.assertTrue(SATISFACTION_VERIFIERS["no_publish_date"](_sig("u", html)))
        self.assertFalse(SATISFACTION_VERIFIERS["no_publish_date"](_sig("u", _BARE_HTML)))

    def test_h1_presence(self):
        self.assertTrue(SATISFACTION_VERIFIERS["no_h1"](_sig("u", "<h1>Title</h1>")))
        self.assertFalse(SATISFACTION_VERIFIERS["no_h1"](_sig("u", _BARE_HTML)))

    def test_trust_links(self):
        html = '<a href="https://www.nih.gov/study">study</a>'
        self.assertTrue(SATISFACTION_VERIFIERS["no_trust_links"](_sig("u", html)))
        self.assertFalse(SATISFACTION_VERIFIERS["no_trust_links"](_sig("u", _BARE_HTML)))


class FilterTests(SimpleTestCase):
    def test_suppresses_task_done_on_the_page(self):
        recs = [{"finding_code": "no_faq_section", "affected_pages": ["https://x.com/a"]}]
        pages = {"https://x.com/a": _sig("https://x.com/a", _FAQ_HTML)}
        kept, suppressed = filter_satisfied(recs, pages)
        self.assertEqual(kept, [])
        self.assertEqual(len(suppressed), 1)

    def test_keeps_task_not_done(self):
        recs = [{"finding_code": "no_faq_section", "affected_pages": ["https://x.com/a"]}]
        pages = {"https://x.com/a": _sig("https://x.com/a", _BARE_HTML)}
        kept, suppressed = filter_satisfied(recs, pages)
        self.assertEqual(len(kept), 1)
        self.assertEqual(suppressed, [])

    def test_requires_satisfied_on_every_affected_page(self):
        # Done on page a, NOT done on page b -> keep (still valid for b).
        recs = [{"finding_code": "no_faq_section", "affected_pages": ["https://x.com/a", "https://x.com/b"]}]
        pages = {
            "https://x.com/a": _sig("https://x.com/a", _FAQ_HTML),
            "https://x.com/b": _sig("https://x.com/b", _BARE_HTML),
        }
        kept, suppressed = filter_satisfied(recs, pages)
        self.assertEqual(len(kept), 1)

    def test_url_normalization_trailing_slash(self):
        recs = [{"finding_code": "no_faq_section", "affected_pages": ["https://x.com/a/"]}]
        pages = {"https://x.com/a": _sig("https://x.com/a", _FAQ_HTML)}
        kept, suppressed = filter_satisfied(recs, pages)
        self.assertEqual(len(suppressed), 1)  # matched despite trailing slash

    def test_unmapped_page_is_kept(self):
        recs = [{"finding_code": "no_faq_section", "affected_pages": ["https://x.com/missing"]}]
        kept, suppressed = filter_satisfied(recs, {})
        self.assertEqual(len(kept), 1)  # can't verify -> keep (safe)

    def test_finding_without_verifier_is_kept(self):
        recs = [{"finding_code": "no_first_hand_experience", "affected_pages": ["https://x.com/a"]}]
        pages = {"https://x.com/a": _sig("https://x.com/a", _BARE_HTML)}
        kept, suppressed = filter_satisfied(recs, pages)
        self.assertEqual(len(kept), 1)  # no verifier -> never suppressed
        self.assertEqual(suppressed, [])
