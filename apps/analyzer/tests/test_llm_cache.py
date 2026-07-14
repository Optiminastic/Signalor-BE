"""
Unit tests for the content-addressed LLM response cache (``pipeline.llm_cache``).

Covers key determinism/uniqueness (the correctness property) and the
enabled/disabled compute behavior. Uses the default LocMem cache — no DB.
"""

import os
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from apps.analyzer.pipeline.llm_cache import (
    build_fingerprint,
    cached_llm,
    response_cache_key,
)

_KEY = dict(feature="competitors", prompt_version="v1", tier="strong", fingerprint="org1:/:abc")


class KeyTests(SimpleTestCase):
    def test_deterministic(self):
        self.assertEqual(response_cache_key(**_KEY), response_cache_key(**_KEY))

    def test_content_change_changes_key(self):
        other = {**_KEY, "fingerprint": "org1:/:DEF"}
        self.assertNotEqual(response_cache_key(**_KEY), response_cache_key(**other))

    def test_prompt_version_bump_changes_key(self):
        other = {**_KEY, "prompt_version": "v2"}
        self.assertNotEqual(response_cache_key(**_KEY), response_cache_key(**other))

    def test_tier_changes_key(self):
        other = {**_KEY, "tier": "cheap"}
        self.assertNotEqual(response_cache_key(**_KEY), response_cache_key(**other))

    def test_key_is_namespaced(self):
        self.assertTrue(response_cache_key(**_KEY).startswith("llmresp:competitors:"))


class FingerprintTests(SimpleTestCase):
    def test_joins_parts(self):
        self.assertEqual(build_fingerprint("org1", "/pricing", "abc"), "org1|/pricing|abc")

    def test_none_is_stable_empty(self):
        self.assertEqual(build_fingerprint("org1", None, "abc"), "org1||abc")


class CachedLlmTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_disabled_is_passthrough(self):
        calls = {"n": 0}

        def compute():
            calls["n"] += 1
            return "result"

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LLM_RESPONSE_CACHE_ENABLED", None)
            self.assertEqual(cached_llm(compute=compute, **_KEY), "result")
            self.assertEqual(cached_llm(compute=compute, **_KEY), "result")
        self.assertEqual(calls["n"], 2)  # never cached

    def test_enabled_caches(self):
        calls = {"n": 0}

        def compute():
            calls["n"] += 1
            return "result"

        with patch.dict(os.environ, {"LLM_RESPONSE_CACHE_ENABLED": "true"}):
            self.assertEqual(cached_llm(compute=compute, **_KEY), "result")
            self.assertEqual(cached_llm(compute=compute, **_KEY), "result")
        self.assertEqual(calls["n"], 1)  # computed once, then served from cache

    def test_none_result_not_cached(self):
        calls = {"n": 0}

        def compute():
            calls["n"] += 1
            return None

        with patch.dict(os.environ, {"LLM_RESPONSE_CACHE_ENABLED": "true"}):
            self.assertIsNone(cached_llm(compute=compute, **_KEY))
            self.assertIsNone(cached_llm(compute=compute, **_KEY))
        self.assertEqual(calls["n"], 2)  # a failed/None result must be retried, not memoized
