"""Tests for the answer block generator.

This is the first part of the product that writes the fix rather than describing
it, so the contracts are about output being *usable*: valid schema, a document
outline that survives pasting, and a mode that matches whether a page exists.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.analyzer.pipeline.schemas import AnswerBlock, FaqPair
from apps.analyzer.services import answer_block as ab

_ASK = "core.llm.structured.ask_structured"


def _run():
    return SimpleNamespace(id=1, brand_name="Acme", url="https://acme.com", organization=None)


def _block(**over):
    base = dict(
        heading="What is generative engine optimization?",
        answer="Generative engine optimization is the practice of structuring a site so AI "
        "answer engines can extract and cite it. It differs from SEO in that the goal is "
        "citation inside an answer, not a ranked link.",
        supporting_points=["Engines retrieve passages, not pages.", "Answer-first text is quoted."],
        faqs=[FaqPair(question="Is GEO different from SEO?", answer="Yes. SEO targets ranked links.")],
        placement="Directly under the hero.",
    )
    base.update(over)
    return AnswerBlock(**base)


def _generate(block, *, page_text="", url=""):
    with patch.object(ab, "_page_excerpt", return_value=page_text), patch.object(
        ab, "_brand_knowledge", return_value=""
    ), patch(_ASK, return_value=block):
        return ab.generate(_run(), "what is GEO?", target_url=url)


class ModeTests(SimpleTestCase):
    """Coverage decides whether we extend a page or start one."""

    def test_existing_page_yields_a_section_to_add(self):
        draft = _generate(_block(), page_text="Some real page copy.", url="https://acme.com/geo")
        self.assertEqual(draft.mode, "add_section")
        self.assertEqual(draft.target_url, "https://acme.com/geo")

    def test_no_page_yields_a_new_page_opening(self):
        draft = _generate(_block())
        self.assertEqual(draft.mode, "new_page")
        self.assertEqual(draft.target_url, "")

    def test_unfetchable_page_is_not_treated_as_a_target(self):
        """Drafting 'add a section to this page' with no page content would invent
        what the page says."""
        draft = _generate(_block(), page_text="", url="https://acme.com/dead")
        self.assertEqual(draft.mode, "new_page")
        self.assertEqual(draft.target_url, "")


class HtmlOutlineTests(SimpleTestCase):
    """Pasting the block must not break the page's heading outline."""

    def test_section_uses_h2_because_the_page_already_has_an_h1(self):
        draft = _generate(_block(), page_text="copy", url="https://acme.com/geo")
        self.assertIn("<h2>", draft.html_snippet)
        self.assertNotIn("<h1>", draft.html_snippet)

    def test_new_page_uses_h1(self):
        self.assertIn("<h1>", _generate(_block()).html_snippet)

    def test_supporting_points_render_as_a_list(self):
        self.assertIn("<li>", _generate(_block()).html_snippet)

    def test_html_is_escaped(self):
        draft = _generate(_block(heading="Tools & <script>alert(1)</script>"))
        self.assertNotIn("<script>alert", draft.html_snippet)
        self.assertIn("&amp;", draft.html_snippet)


class FaqSchemaTests(SimpleTestCase):
    """JSON-LD is built in Python, so it must actually be valid."""

    def test_schema_is_parseable_and_correctly_typed(self):
        import json

        raw = ab.build_faq_jsonld(
            [{"question": "Q?", "answer": "A."}, {"question": "Q2?", "answer": "A2."}], wrap=False
        )
        data = json.loads(raw)
        self.assertEqual(data["@type"], "FAQPage")
        self.assertEqual(len(data["mainEntity"]), 2)
        self.assertEqual(data["mainEntity"][0]["acceptedAnswer"]["@type"], "Answer")

    def test_wrapped_output_is_a_script_tag(self):
        out = ab.build_faq_jsonld([{"question": "Q?", "answer": "A."}])
        self.assertTrue(out.startswith('<script type="application/ld+json">'))
        self.assertTrue(out.rstrip().endswith("</script>"))

    def test_incomplete_pairs_are_dropped(self):
        import json

        raw = ab.build_faq_jsonld(
            [{"question": "Q?", "answer": ""}, {"question": "Ok?", "answer": "Yes."}], wrap=False
        )
        self.assertEqual(len(json.loads(raw)["mainEntity"]), 1)

    def test_no_faqs_yields_no_schema_rather_than_an_empty_shell(self):
        self.assertEqual(ab.build_faq_jsonld([]), "")

    def test_draft_carries_its_schema(self):
        self.assertIn("FAQPage", _generate(_block()).faq_jsonld)


class FailSoftTests(SimpleTestCase):
    def test_blank_prompt_is_rejected(self):
        self.assertIsNone(ab.generate(_run(), "   "))

    def test_model_returning_nothing_is_not_an_error(self):
        with patch.object(ab, "_page_excerpt", return_value=""), patch.object(
            ab, "_brand_knowledge", return_value=""
        ), patch(_ASK, return_value=None):
            self.assertIsNone(ab.generate(_run(), "q"))

    def test_an_empty_answer_is_rejected(self):
        """A heading with no answer is not a usable block."""
        self.assertIsNone(_generate(_block(answer="   ")))

    def test_blank_supporting_points_and_faqs_are_stripped(self):
        draft = _generate(
            _block(
                supporting_points=["real point", "   "],
                faqs=[FaqPair(question="  ", answer="orphan")],
            )
        )
        self.assertEqual(draft.supporting_points, ["real point"])
        self.assertEqual(draft.faqs, [])

    def test_draft_is_json_serializable(self):
        import json

        json.dumps(_generate(_block()).as_dict())


class CoverageIntegrationTests(SimpleTestCase):
    """generate_for_prompt picks the mode from coverage without the caller deciding."""

    def _track(self):
        return SimpleNamespace(
            analysis_run=_run(), prompt_text="what is GEO?", intent="informational"
        )

    def test_covered_prompt_targets_the_matched_page(self):
        from apps.analyzer.services import prompt_coverage as pc

        row = pc.PromptCoverage(1, "q", "", pc.COVERED, best_url="https://acme.com/geo")
        with patch.object(ab, "generate", return_value=None) as gen, patch(
            "apps.analyzer.services.prompt_coverage.coverage_for_run", return_value=[row]
        ):
            ab.generate_for_prompt(self._track())
        self.assertEqual(gen.call_args.kwargs["target_url"], "https://acme.com/geo")

    def test_uncovered_prompt_gets_no_target(self):
        from apps.analyzer.services import prompt_coverage as pc

        row = pc.PromptCoverage(1, "q", "", pc.UNCOVERED)
        with patch.object(ab, "generate", return_value=None) as gen, patch(
            "apps.analyzer.services.prompt_coverage.coverage_for_run", return_value=[row]
        ):
            ab.generate_for_prompt(self._track())
        self.assertEqual(gen.call_args.kwargs["target_url"], "")

    def test_a_failing_coverage_lookup_still_drafts(self):
        with patch.object(ab, "generate", return_value=None) as gen, patch(
            "apps.analyzer.services.prompt_coverage.coverage_for_run",
            side_effect=RuntimeError("db down"),
        ):
            ab.generate_for_prompt(self._track())
        self.assertEqual(gen.call_args.kwargs["target_url"], "")
