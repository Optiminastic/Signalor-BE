"""Tests for the off-page presence verifier (pipeline/offpage_presence.py).

The Serper search and entity-confidence check are mocked, so these run without
network and assert the orchestration + fail-safe behaviour.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.analyzer.pipeline import offpage_presence
from apps.analyzer.pipeline.offpage_presence import brand_present_on_domain


class OffpagePresenceTests(SimpleTestCase):
    def test_unknown_without_serper_key(self):
        # No search configured → None (UNKNOWN) so the caller keeps the task.
        with patch.object(offpage_presence.serper, "is_configured", return_value=False):
            self.assertIsNone(brand_present_on_domain("Vercel", "techcrunch.com"))

    def test_unknown_on_search_error(self):
        with patch.object(offpage_presence.serper, "is_configured", return_value=True), patch.object(
            offpage_presence.serper, "search", side_effect=RuntimeError("boom")
        ):
            self.assertIsNone(brand_present_on_domain("Vercel", "techcrunch.com"))

    def test_present_when_a_result_matches_the_brand(self):
        results = {"organic": [{"title": "Vercel raises", "snippet": "Vercel the platform", "link": "x"}]}
        with patch.object(offpage_presence.serper, "is_configured", return_value=True), patch.object(
            offpage_presence.serper, "search", return_value=results
        ), patch.object(offpage_presence, "compute_entity_confidence", return_value=0.9):
            self.assertIs(brand_present_on_domain("Vercel", "techcrunch.com"), True)

    def test_absent_when_no_results(self):
        with patch.object(offpage_presence.serper, "is_configured", return_value=True), patch.object(
            offpage_presence.serper, "search", return_value={"organic": []}
        ):
            self.assertIs(brand_present_on_domain("MadeUpStartupXYZ", "techcrunch.com"), False)

    def test_absent_when_only_name_collisions(self):
        # Results exist but none plausibly refer to this brand (low confidence).
        results = {"organic": [{"title": "signal processing", "snippet": "…", "link": "x"}]}
        with patch.object(offpage_presence.serper, "is_configured", return_value=True), patch.object(
            offpage_presence.serper, "search", return_value=results
        ), patch.object(offpage_presence, "compute_entity_confidence", return_value=0.1):
            self.assertIs(brand_present_on_domain("Signal", "techcrunch.com"), False)

    def test_empty_inputs_return_none(self):
        self.assertIsNone(brand_present_on_domain("", "techcrunch.com"))
        self.assertIsNone(brand_present_on_domain("Vercel", ""))
