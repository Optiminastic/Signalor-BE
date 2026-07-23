"""Tests for run-level recommendation assembly (pipeline/rec_aggregate.py)."""

from django.test import SimpleTestCase

from apps.analyzer.pipeline.rec_aggregate import (
    attach_evidence,
    build_run_recommendations,
    dedupe_recommendations,
    ground_description,
)


class EvidenceTests(SimpleTestCase):
    def test_attach_pulls_nested_and_flat_keys(self):
        details = {
            "content": {"checks": {
                "coverage_depth": {"citation_count": 0, "word_count": 1240},
            }},
        }
        rec = {"finding_code": "no_citations"}
        attach_evidence(rec, details)
        self.assertEqual(rec["evidence"]["citation_count"], 0)
        self.assertEqual(rec["evidence"]["word_count"], 1240)

    def test_unmapped_finding_gets_empty_evidence(self):
        rec = {"finding_code": "totally_unknown"}
        attach_evidence(rec, {"content": {"checks": {"word_count": 9}}})
        self.assertEqual(rec["evidence"], {})

    def test_ground_description_prepends_real_numbers(self):
        rec = {
            "finding_code": "no_citations",
            "description": "No citations found.",
            "evidence": {"citation_count": 0, "word_count": 1240},
        }
        ground_description(rec)
        self.assertTrue(rec["description"].startswith("This page has 0 citations across 1240 words."))

    def test_ground_description_noop_without_evidence(self):
        rec = {"finding_code": "no_citations", "description": "x", "evidence": {}}
        ground_description(rec)
        self.assertEqual(rec["description"], "x")


class DedupeTests(SimpleTestCase):
    def test_same_finding_across_pages_collapses_to_one(self):
        recs = [
            {"finding_code": "no_publish_date", "pillar": "eeat", "impact_points": 1.0,
             "evidence": {}, "_page_url": "https://x.com/a"},
            {"finding_code": "no_publish_date", "pillar": "eeat", "impact_points": 2.0,
             "evidence": {"publish_date": False}, "_page_url": "https://x.com/b"},
            {"finding_code": "no_publish_date", "pillar": "eeat", "impact_points": 0.5,
             "evidence": {}, "_page_url": "https://x.com/c"},
        ]
        out = dedupe_recommendations(recs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["impact_points"], 2.0)  # strongest survives
        self.assertEqual(out[0]["affected_pages"],
                         ["https://x.com/a", "https://x.com/b", "https://x.com/c"])
        self.assertNotIn("_page_url", out[0])  # transient tag removed

    def test_different_findings_are_preserved(self):
        recs = [
            {"finding_code": "no_h1", "pillar": "content", "impact_points": 1.0, "_page_url": "u1"},
            {"finding_code": "no_faq_section", "pillar": "content", "impact_points": 1.0, "_page_url": "u1"},
        ]
        out = dedupe_recommendations(recs)
        self.assertEqual(len(out), 2)


def _content_details(word_count: int, citations: int) -> dict:
    return {
        "content": {
            "findings": ["no_citations"],
            "checks": {
                "coverage_depth": {"citation_count": citations, "word_count": word_count},
                "coverage_score": 0.0,
            },
        },
    }


class BuildTests(SimpleTestCase):
    def test_homepage_and_pages_dedupe_into_single_grounded_task(self):
        homepage = _content_details(1240, 0)
        pages = [
            {"url": "https://x.com/blog", "content_score": 10, "schema_score": 0,
             "content_details": _content_details(300, 0)["content"], "schema_details": {}},
        ]
        recs = build_run_recommendations(
            homepage, pages, {"content": 20.0, "schema": 0.0},
            industry="default", run_url="https://x.com/",
        )
        citation_recs = [r for r in recs if r["finding_code"] == "no_citations"]
        self.assertEqual(len(citation_recs), 1)  # deduped across homepage + blog
        rec = citation_recs[0]
        self.assertGreaterEqual(len(rec["affected_pages"]), 1)
        self.assertIn("This page has", rec["description"])
        self.assertGreater(rec["impact_points"], 0.0)
        # Clean model-kwargs only (no transient keys that would break Recommendation()).
        self.assertNotIn("_page_url", rec)
        self.assertNotIn("affected_count", rec)

    def test_extra_recs_are_folded_in(self):
        homepage = {"technical": {"findings": [], "checks": {}}}
        extra = [{"finding_code": "siteone_broken_links", "pillar": "technical",
                  "priority": "high", "title": "Fix broken links", "description": "",
                  "action": "", "category": "technical"}]
        recs = build_run_recommendations(
            homepage, [], {}, run_url="https://x.com/", extra_recs=extra,
        )
        self.assertTrue(any(r["finding_code"] == "siteone_broken_links" for r in recs))
