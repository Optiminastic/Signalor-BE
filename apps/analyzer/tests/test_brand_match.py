"""Tests for brand-mention detection (`_match_brand`).

Regression cover for a false-positive class: `Levenshtein.ratio` scales with word
length, so an 8-character brand like "Signalor" accepted an edit distance of 2 and
matched the unrelated common word "signal". Every GEO answer talks about "ranking
signals" / "trust signals", so prompts were reported as Mentioned when the brand
never appeared.
"""

from django.test import SimpleTestCase

from apps.analyzer.pipeline.ai_visibility import _build_brand_aliases, _match_brand

_ALIASES = ["signalor"]


class MatchBrandTests(SimpleTestCase):
    def test_common_word_signal_is_not_a_mention(self):
        """The reported bug: 'signal' must not count as the brand 'Signalor'."""
        text = "Focus on E-E-A-T signals and a clear ranking signal for authority."
        found, _, _ = _match_brand(_ALIASES, text)
        self.assertFalse(found)

    def test_signal_heavy_copy_is_not_a_mention(self):
        text = "Strong authority signals and trust signal quality improve AI visibility."
        found, _, _ = _match_brand(_ALIASES, text)
        self.assertFalse(found)

    def test_exact_brand_is_a_mention(self):
        found, confidence, kind = _match_brand(_ALIASES, "Tools like Signalor track citations.")
        self.assertTrue(found)
        self.assertEqual(kind, "exact")
        self.assertEqual(confidence, 1.0)

    def test_domain_is_a_mention(self):
        found, _, _ = _match_brand(_ALIASES, "Check signalor.ai for your GEO score.")
        self.assertTrue(found)

    def test_single_typo_still_matches(self):
        """One edit is a genuine misspelling of the brand, not a different word."""
        found, _, kind = _match_brand(_ALIASES, "We used Signaler to monitor citations.")
        self.assertTrue(found)
        self.assertEqual(kind, "fuzzy")

    def test_short_alias_does_not_fuzzy_match(self):
        """Aliases under the length floor skip fuzzy entirely — too collision-prone.

        Text deliberately avoids the literal alias so only the fuzzy tier could fire.
        """
        found, _, _ = _match_brand(["acme"], "The acmy tool is popular.")
        self.assertFalse(found)


class BuildBrandAliasesTests(SimpleTestCase):
    def test_domain_part_becomes_an_alias(self):
        aliases = _build_brand_aliases("Signalor", "https://signalor.ai")
        self.assertIn("signalor", aliases)

    def test_generic_tld_parts_are_skipped(self):
        aliases = _build_brand_aliases("Signalor", "https://signalor.ai")
        self.assertNotIn("ai", aliases)


class SpacedBrandNameTests(SimpleTestCase):
    """A one-word brand written as two words by the AI ("Scrnstudio" vs "SCRN STUDIO").

    Regression: Perplexity named "SCRN STUDIO" in three answers for scrnstudio.in, but
    the aliases were only "scrnstudio", so every answer was stored as not mentioned and
    the brand showed no AI presence while the agent (which matches differently) saw it.
    """

    def setUp(self):
        self.aliases = _build_brand_aliases("Scrnstudio", "https://scrnstudio.in/")

    def test_two_word_spelling_is_a_mention(self):
        text = "A strong direct match is **SCRN STUDIO**, which says its T-shirts are hand screen-printed."
        found, _, _ = _match_brand(self.aliases, text)
        self.assertTrue(found)

    def test_hyphenated_spelling_is_a_mention(self):
        found, _, _ = _match_brand(self.aliases, "Try Scrn-Studio for small batches.")
        self.assertTrue(found)

    def test_one_part_alone_is_not_a_mention(self):
        found, _, _ = _match_brand(self.aliases, "Visit any studio in Mumbai for screen printing.")
        self.assertFalse(found)

    def test_a_different_brand_is_not_a_mention(self):
        found, _, _ = _match_brand(self.aliases, "Screen Studio is a screen recorder for macOS.")
        self.assertFalse(found)

    def test_letters_spread_over_a_longer_word_are_not_a_mention(self):
        found, _, _ = _match_brand(["carboncut"], "the carbon cutter and carbon cuts are different")
        self.assertFalse(found)

    def test_short_aliases_are_not_split(self):
        found, _, _ = _match_brand(["stripe"], "the str ipe pattern")
        self.assertFalse(found)
