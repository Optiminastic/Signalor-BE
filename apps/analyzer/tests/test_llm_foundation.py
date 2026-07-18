"""
Unit tests for Epic 1 (LLM Foundation): the standardized LLM interface.

Pure-logic tests -- no DB, no network -- so they subclass ``SimpleTestCase``.
Covers the shared JSON extractor, response schemas, tier routing, JSON-mode
gating, and system-message construction.
"""

from django.test import SimpleTestCase

from apps.analyzer.pipeline import llm
from apps.analyzer.pipeline.schemas import MetaFix, PromptList
from apps.analyzer.pipeline.structured import (
    _is_object_schema,
    extract_json,
    strip_code_fences,
)


class ExtractJsonTests(SimpleTestCase):
    def test_plain_object(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_object(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_chatty_object(self):
        self.assertEqual(extract_json('Sure, here:\n{"a": 1}\nHope that helps'), {"a": 1})

    def test_array_with_expect_list(self):
        self.assertEqual(extract_json('```\n["x", "y"]\n```', expect=list), ["x", "y"])

    def test_json_ld_script_body(self):
        # The auto_fix schema path relies on pulling the {...} out of a <script> tag.
        raw = '<script type="application/ld+json">{"@type": "Organization"}</script>'
        self.assertEqual(extract_json(raw), {"@type": "Organization"})

    def test_garbage_returns_none(self):
        self.assertIsNone(extract_json("not json at all"))

    def test_empty_returns_none(self):
        self.assertIsNone(extract_json(""))

    def test_strip_code_fences_idempotent(self):
        once = strip_code_fences("```json\n{}\n```")
        self.assertEqual(once, "{}")
        self.assertEqual(strip_code_fences(once), "{}")


class SchemaTests(SimpleTestCase):
    def test_metafix_seo_keys(self):
        mf = MetaFix.model_validate({"seo_title": "T", "seo_description": "D"})
        self.assertEqual((mf.seo_title, mf.seo_description), ("T", "D"))

    def test_metafix_title_aliases(self):
        mf = MetaFix.model_validate({"title": "T2", "description": "D2"})
        self.assertEqual((mf.seo_title, mf.seo_description), ("T2", "D2"))

    def test_metafix_missing_field_raises(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            MetaFix.model_validate({"seo_title": "only"})

    def test_promptlist_root(self):
        pl = PromptList.model_validate(["a", "b", "c"])
        self.assertEqual(pl.root, ["a", "b", "c"])

    def test_is_object_schema(self):
        self.assertTrue(_is_object_schema(MetaFix))
        self.assertFalse(_is_object_schema(PromptList))


class TierRoutingTests(SimpleTestCase):
    def test_supports_json_object(self):
        self.assertTrue(llm._supports_json_object("openai/gpt-4o-mini"))
        self.assertTrue(llm._supports_json_object("google/gemini-2.5-flash"))
        self.assertFalse(llm._supports_json_object("anthropic/claude-haiku-4.5"))

    def test_tier_maps_to_registry_model(self):
        self.assertEqual(llm._pick_model(None, "cheap"), llm.MODELS["gemini"])
        self.assertEqual(llm._pick_model(None, "medium"), llm.MODELS["claude"])
        self.assertEqual(llm._pick_model(None, "strong"), llm.MODELS["sonnet"])

    def test_preferred_beats_tier(self):
        self.assertEqual(llm._pick_model("opus", "cheap"), llm.MODELS["opus"])

    def test_unknown_tier_falls_through_to_rotation(self):
        self.assertIn(llm._pick_model(None, "nope"), llm.MODELS.values())


class MessageBuilderTests(SimpleTestCase):
    def test_system_message_prepended(self):
        msgs = llm._build_messages("hi", "be terse")
        self.assertEqual(msgs[0], {"role": "system", "content": "be terse"})
        self.assertEqual(msgs[1], {"role": "user", "content": "hi"})

    def test_no_system_yields_user_only(self):
        self.assertEqual(llm._build_messages("hi", None), [{"role": "user", "content": "hi"}])
