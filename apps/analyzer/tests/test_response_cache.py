"""Tests for the semantic response cache (Epic 7).

The pgvector search (``_semantic_search``) is Postgres-only, so it is mocked; the exact-hash
tier, scope isolation, expiry and fail-soft behavior are exercised for real on SQLite.
``embed_query`` is mocked throughout so no test ever calls the embedding API.

Scope isolation is the safety property: a cached answer must never cross brands/purposes.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.analyzer.models import LLMResponseCache
from apps.analyzer.pipeline import response_cache
from apps.organizations.models import Organization

_SEARCH = "apps.analyzer.pipeline.response_cache._semantic_search"
_EMBED_Q = "apps.analyzer.pipeline.embeddings.embed_query"
_SCOPE = dict(purpose="Generate Brand Prompts", model_key="cheap:t0.0:m1200")


class _CacheTestBase(TestCase):
    """Mocks the embedding API for every cache test (store() embeds the prompt)."""

    def setUp(self):
        p = patch(_EMBED_Q, return_value=[0.1] * 768)
        p.start()
        self.addCleanup(p.stop)


class ExactHashTests(_CacheTestBase):
    def setUp(self):
        super().setUp()
        self.org = Organization.objects.create(name="Acme", url="https://acme.com", owner_email="o@x.com")

    def test_store_then_exact_hit(self):
        response_cache.store("What is Acme?", "Acme is a widget maker.", org=self.org, **_SCOPE)
        with patch(_SEARCH) as ms:  # the exact tier must not need the vector search
            hit = response_cache.lookup("What is Acme?", org=self.org, **_SCOPE)
        self.assertEqual(hit, "Acme is a widget maker.")
        ms.assert_not_called()

    def test_whitespace_normalized_prompt_still_hits(self):
        response_cache.store("What is   Acme?", "R", org=self.org, **_SCOPE)
        with patch(_SEARCH):
            self.assertEqual(response_cache.lookup("What is Acme?", org=self.org, **_SCOPE), "R")

    def test_hit_count_increments(self):
        response_cache.store("p", "r", org=self.org, **_SCOPE)
        with patch(_SEARCH, return_value=(None, 0.0)):
            response_cache.lookup("p", org=self.org, **_SCOPE)
        self.assertEqual(LLMResponseCache.objects.get().hit_count, 1)

    def test_miss_returns_none(self):
        with patch(_SEARCH, return_value=(None, 0.0)):
            self.assertIsNone(response_cache.lookup("nothing stored", org=self.org, **_SCOPE))

    def test_restoring_same_prompt_updates_not_duplicates(self):
        response_cache.store("p", "first", org=self.org, **_SCOPE)
        response_cache.store("p", "second", org=self.org, **_SCOPE)
        self.assertEqual(LLMResponseCache.objects.count(), 1)
        self.assertEqual(LLMResponseCache.objects.get().response_text, "second")


class ScopeIsolationTests(_CacheTestBase):
    """The safety property: entries never leak across org / purpose / model."""

    def setUp(self):
        super().setUp()
        self.a = Organization.objects.create(name="A", url="https://a.com", owner_email="a@x.com")
        self.b = Organization.objects.create(name="B", url="https://b.com", owner_email="b@x.com")
        response_cache.store("same prompt", "A's answer", org=self.a, **_SCOPE)

    def test_other_org_does_not_hit(self):
        with patch(_SEARCH, return_value=(None, 0.0)):
            self.assertIsNone(response_cache.lookup("same prompt", org=self.b, **_SCOPE))

    def test_null_org_does_not_hit_an_org_scoped_entry(self):
        with patch(_SEARCH, return_value=(None, 0.0)):
            self.assertIsNone(response_cache.lookup("same prompt", org=None, **_SCOPE))

    def test_other_purpose_does_not_hit(self):
        with patch(_SEARCH, return_value=(None, 0.0)):
            self.assertIsNone(
                response_cache.lookup(
                    "same prompt", purpose="Other", model_key=_SCOPE["model_key"], org=self.a
                )
            )

    def test_other_model_key_does_not_hit(self):
        with patch(_SEARCH, return_value=(None, 0.0)):
            self.assertIsNone(
                response_cache.lookup(
                    "same prompt", purpose=_SCOPE["purpose"], model_key="strong:t0.0:m1200", org=self.a
                )
            )


class SemanticTierTests(_CacheTestBase):
    def setUp(self):
        super().setUp()
        self.org = Organization.objects.create(name="Acme", url="https://acme.com", owner_email="o@x.com")
        response_cache.store("original prompt", "cached answer", org=self.org, **_SCOPE)
        self.entry = LLMResponseCache.objects.get()

    def test_similarity_at_or_above_floor_hits(self):
        with patch(_SEARCH, return_value=(self.entry, 0.98)):
            self.assertEqual(response_cache.lookup("near identical", org=self.org, **_SCOPE), "cached answer")

    def test_similarity_below_floor_misses(self):
        with patch(_SEARCH, return_value=(self.entry, 0.90)):
            self.assertIsNone(response_cache.lookup("merely similar", org=self.org, **_SCOPE))


class ExpiryAndFlagTests(_CacheTestBase):
    def setUp(self):
        super().setUp()
        self.org = Organization.objects.create(name="Acme", url="https://acme.com", owner_email="o@x.com")

    def test_expired_entry_is_not_returned(self):
        response_cache.store("p", "r", org=self.org, **_SCOPE)
        LLMResponseCache.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        with patch(_SEARCH, return_value=(None, 0.0)):
            self.assertIsNone(response_cache.lookup("p", org=self.org, **_SCOPE))

    def test_kill_switch_disables_lookup_and_store(self):
        with self.settings(SIGNALOR_ENABLE_SEMANTIC_CACHE=False):
            response_cache.store("p", "r", org=self.org, **_SCOPE)
            self.assertEqual(LLMResponseCache.objects.count(), 0)
            self.assertIsNone(response_cache.lookup("p", org=self.org, **_SCOPE))

    def test_lookup_is_fail_soft(self):
        with patch(_SEARCH, side_effect=RuntimeError):
            self.assertIsNone(response_cache.lookup("p", org=self.org, **_SCOPE))

    def test_store_is_fail_soft(self):
        with patch(_EMBED_Q, side_effect=RuntimeError):
            response_cache.store("p", "r", org=self.org, **_SCOPE)  # must not raise
        self.assertEqual(LLMResponseCache.objects.count(), 0)
