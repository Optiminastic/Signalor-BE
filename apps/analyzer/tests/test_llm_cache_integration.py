"""ask_llm(cache=...) integration with the response cache (Epic 7)."""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.analyzer.pipeline import llm

_CALL = "apps.analyzer.pipeline.llm.ask_llm_with_citations"
_LOOKUP = "apps.analyzer.pipeline.response_cache.lookup"
_STORE = "apps.analyzer.pipeline.response_cache.store"


class CacheKeyTests(SimpleTestCase):
    def test_system_prompt_is_part_of_the_cache_key(self):
        # Two brands can share a user prompt but differ by brand card -> must not collide.
        a = llm._cache_prompt_key("same", "BRAND CARD A")
        b = llm._cache_prompt_key("same", "BRAND CARD B")
        self.assertNotEqual(a, b)

    def test_model_key_separates_routing_and_params(self):
        self.assertNotEqual(
            llm._cache_model_key(None, "cheap", 0.0, 1200),
            llm._cache_model_key(None, "strong", 0.0, 1200),
        )
        self.assertNotEqual(
            llm._cache_model_key(None, "cheap", 0.0, 1200),
            llm._cache_model_key(None, "cheap", 0.7, 1200),
        )


class AskLlmCacheTests(TestCase):
    def test_cache_off_by_default_never_touches_cache(self):
        with (
            patch(_CALL, return_value=("fresh", [])) as mock_call,
            patch(_LOOKUP) as ml,
            patch(_STORE) as ms,
        ):
            out = llm.ask_llm("p", purpose="x")
        self.assertEqual(out, "fresh")
        mock_call.assert_called_once()
        ml.assert_not_called()
        ms.assert_not_called()

    def test_cache_miss_calls_llm_and_stores(self):
        with (
            patch(_CALL, return_value=("fresh", [])) as mock_call,
            patch(_LOOKUP, return_value=None),
            patch(_STORE) as ms,
        ):
            out = llm.ask_llm("p", purpose="x", cache=True)
        self.assertEqual(out, "fresh")
        mock_call.assert_called_once()
        ms.assert_called_once()

    def test_cache_hit_skips_the_llm_entirely(self):
        with (
            patch(_CALL) as mock_call,
            patch(_LOOKUP, return_value="cached"),
            patch(_STORE) as ms,
        ):
            out = llm.ask_llm("p", purpose="x", cache=True)
        self.assertEqual(out, "cached")
        mock_call.assert_not_called()  # the whole point: no LLM call, no cost
        ms.assert_not_called()

    def test_empty_response_is_not_stored(self):
        with patch(_CALL, return_value=("", [])), patch(_LOOKUP, return_value=None), patch(_STORE) as ms:
            llm.ask_llm("p", purpose="x", cache=True)
        ms.assert_not_called()
