"""Tracked prompts must be what a *prospect* types, not what a fan types.

The bug this pins: onboarding stored five branded prompts — "What is <brand> and
what do they do?", "How much does <brand> cost?", "<brand> reviews and
reputation" — and `_save_probes_and_tracks` prefers stored prompts over generated
ones, so the good generator never ran. Those prompts scored 88-100% visibility
and measured nothing: handed the brand name, an engine repeats it.

The number that matters is the reverse. A buyer describes their problem, never the
brand, and either it gets cited or it does not. On that question 0% is real
information and a rising score is real progress.
"""

from django.test import SimpleTestCase

from apps.analyzer.pipeline.prompt_tracker import _drop_branded

BRAND = "Signalor"
# The exact set production was tracking.
PRODUCTION_SET = [
    "What is Signalor and what do they do?",
    "Is Signalor a good choice for small businesses?",
    "What are the best alternatives to Signalor?",
    "How much does Signalor cost?",
    "Signalor reviews and reputation",
]
# What the customer actually asked for.
ICP_SET = [
    "What are the best GEO SEO tools for ecommerce?",
    "How can I track GEO citations of my website?",
    "How can I get sales from ChatGPT?",
]


class DropBrandedTests(SimpleTestCase):
    def test_icp_prompts_all_survive(self):
        self.assertEqual(_drop_branded(ICP_SET, BRAND), ICP_SET)

    def test_the_production_set_collapses_to_one_baseline(self):
        """5 branded prompts in, 1 out — the rest were measuring nothing."""
        kept = _drop_branded(PRODUCTION_SET, BRAND)
        self.assertEqual(len(kept), 1)

    def test_one_branded_prompt_is_kept_deliberately(self):
        """If engines cannot resolve the name, category ranking cannot help."""
        kept = _drop_branded([*ICP_SET, f"What is {BRAND}?"], BRAND)
        self.assertIn(f"What is {BRAND}?", kept)
        self.assertEqual(len(kept), len(ICP_SET) + 1)

    def test_unbranded_prompts_come_first(self):
        """They are the ones worth reading; the baseline is a footnote."""
        kept = _drop_branded([f"What is {BRAND}?", *ICP_SET], BRAND)
        self.assertEqual(kept[:-1], ICP_SET)

    def test_matching_is_case_insensitive(self):
        kept = _drop_branded(["how does SIGNALOR compare?", *ICP_SET], BRAND)
        self.assertNotIn("how does SIGNALOR compare?", kept[: len(ICP_SET)])

    def test_the_brand_name_inside_a_longer_word_still_counts(self):
        self.assertEqual(len(_drop_branded(["is signalor.ai any good?"], BRAND)), 1)

    def test_an_empty_brand_name_is_a_pass_through(self):
        self.assertEqual(_drop_branded(ICP_SET, ""), ICP_SET)

    def test_an_all_branded_set_still_returns_something(self):
        """Never hand back an empty tracked set — that breaks the whole page."""
        self.assertEqual(len(_drop_branded(PRODUCTION_SET, BRAND)), 1)


class TemplateContractTests(SimpleTestCase):
    """The instruction has to survive edits, since it is what the model reads."""

    def _template(self) -> str:
        from pathlib import Path

        import apps.analyzer.prompts as pkg

        return (
            Path(pkg.__file__).parent / "templates" / "brand_prompts" / "v1.j2"
        ).read_text()

    def test_it_forbids_the_brand_name(self):
        t = self._template()
        self.assertIn("Never", t)
        self.assertIn("brand_name", t)

    def test_it_teaches_the_icp_shapes_by_example(self):
        """Abstract rules drifted; concrete good/bad pairs are what worked."""
        t = self._template()
        for shape in ("CATEGORY + QUALIFIER", "TASK / HOW-TO", "OUTCOME", "PROBLEM"):
            with self.subTest(shape=shape):
                self.assertIn(shape, t)

    def test_it_shows_a_bad_example_next_to_each_good_one(self):
        self.assertGreaterEqual(self._template().count("Not:"), 3)

    def test_it_explains_why_branded_prompts_are_worthless(self):
        """Without the reason, the rule reads as arbitrary and gets ignored."""
        self.assertIn("100% visibility", self._template())

    def test_it_bans_generic_prompts_explicitly(self):
        t = self._template()
        self.assertIn("generic", t.lower())
        self.assertIn("identically for a completely different company", t)


class FallbackTests(SimpleTestCase):
    def test_the_generator_fallback_is_not_branded(self):
        from unittest.mock import patch

        from apps.analyzer.pipeline import prompt_tracker

        with patch.object(prompt_tracker, "ask_structured", return_value=None, create=True):
            with patch("core.llm.structured.ask_structured", return_value=None):
                out = prompt_tracker.generate_brand_prompts(
                    brand_name=BRAND, brand_url="https://signalor.ai", industry="GEO tools"
                )
        for p in out:
            with self.subTest(prompt=p):
                self.assertNotIn(BRAND.lower(), p.lower())
