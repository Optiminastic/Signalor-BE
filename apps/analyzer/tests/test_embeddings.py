"""Tests for the knowledge-base embedding client.

Two things broke here in practice and both were silent:

1. ``text-embedding-004`` was retired by Google, so every embed call failed and
   the corpus simply stayed empty. Everything downstream has an ``or ""``
   fallback, so nothing surfaced.
2. Its replacements default to 3072 dimensions while ``BrandCorpusChunk.embedding``
   is a fixed 768-wide pgvector column. Document and query vectors must therefore
   be pinned to the same width - a mismatch makes every similarity comparison
   either error or score nothing.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.analyzer.pipeline import embeddings as emb


class DimensionContractTests(SimpleTestCase):
    def test_target_width_matches_the_database_column(self):
        from apps.organizations.models import EMBEDDING_DIMENSIONS

        self.assertEqual(emb.EMBED_DIMENSIONS, EMBEDDING_DIMENSIONS)

    def test_the_width_is_taken_from_the_column_not_a_literal(self):
        """A duplicated 768 drifts; the column is the only authority."""
        from unittest.mock import patch

        with patch.dict("os.environ", {"CORPUS_EMBED_DIMENSIONS": ""}, clear=False):
            from apps.organizations.models import EMBEDDING_DIMENSIONS

            self.assertEqual(emb._embed_dimensions(), EMBEDDING_DIMENSIONS)

    def test_a_mismatched_override_is_rejected_at_import_not_at_insert(self):
        """Otherwise a whole run of embeddings is paid for before the INSERT fails."""
        from unittest.mock import patch

        from django.core.exceptions import ImproperlyConfigured

        with patch.dict("os.environ", {"CORPUS_EMBED_DIMENSIONS": "1536"}, clear=False):
            with self.assertRaises(ImproperlyConfigured):
                emb._embed_dimensions()

    def test_a_malformed_override_is_rejected(self):
        from unittest.mock import patch

        from django.core.exceptions import ImproperlyConfigured

        with patch.dict("os.environ", {"CORPUS_EMBED_DIMENSIONS": "wide"}, clear=False):
            with self.assertRaises(ImproperlyConfigured):
                emb._embed_dimensions()

    def test_a_matching_override_is_accepted(self):
        from unittest.mock import patch

        from apps.organizations.models import EMBEDDING_DIMENSIONS

        with patch.dict(
            "os.environ", {"CORPUS_EMBED_DIMENSIONS": str(EMBEDDING_DIMENSIONS)}, clear=False
        ):
            self.assertEqual(emb._embed_dimensions(), EMBEDDING_DIMENSIONS)

    def test_retired_model_is_not_the_default(self):
        self.assertNotIn("text-embedding-004", emb.DEFAULT_EMBED_MODEL)

    def test_oversized_vector_is_truncated_to_the_column_width(self):
        self.assertEqual(len(emb._fit([0.1] * 3072)), emb.EMBED_DIMENSIONS)

    def test_exact_width_passes_through(self):
        self.assertEqual(len(emb._fit([0.1] * emb.EMBED_DIMENSIONS)), emb.EMBED_DIMENSIONS)

    def test_short_vector_is_dropped_not_padded(self):
        """A padded vector would be stored and score as a poor match forever."""
        self.assertIsNone(emb._fit([0.1] * 10))

    def test_none_is_passed_through(self):
        self.assertIsNone(emb._fit(None))


class WidthRequestTests(SimpleTestCase):
    class _Fake:
        def __init__(self):
            self.kwargs = None

        def embed_content(self, **kwargs):
            self.kwargs = kwargs
            return {"embedding": [0.1] * emb.EMBED_DIMENSIONS}

    def test_output_dimensionality_is_requested(self):
        fake = self._Fake()
        emb._embed_content(fake, model="m", content="t", task_type="retrieval_query")
        self.assertEqual(fake.kwargs["output_dimensionality"], emb.EMBED_DIMENSIONS)

    def test_older_sdk_without_the_kwarg_still_works(self):
        class _Old:
            calls = 0

            def embed_content(self, **kwargs):
                _Old.calls += 1
                if "output_dimensionality" in kwargs:
                    raise TypeError("unexpected keyword argument")
                return {"embedding": [0.1] * 3072}

        old = _Old()
        resp = emb._embed_content(old, model="m", content="t", task_type="retrieval_query")
        self.assertEqual(_Old.calls, 2)  # tried with, retried without
        self.assertEqual(len(emb._fit(resp["embedding"])), emb.EMBED_DIMENSIONS)


class QueryDocumentParityTests(SimpleTestCase):
    """The bug that would have made retrieval silently return nothing."""

    def _stub(self, width):
        class _Fake:
            @staticmethod
            def embed_content(**kwargs):
                # Ignore the requested width to simulate a model that returns its native size.
                return {"embedding": [0.1] * width}

        return _Fake()

    def test_query_and_document_vectors_are_the_same_width(self):
        with patch.object(emb, "_configure", return_value=self._stub(3072)):
            q = emb.embed_query("what is GEO?")
            d = emb.embed_documents(["GEO is ..."])
        self.assertEqual(len(q), emb.EMBED_DIMENSIONS)
        self.assertEqual(len(d[0]), emb.EMBED_DIMENSIONS)
        self.assertEqual(len(q), len(d[0]))

    def test_missing_key_returns_none_not_a_crash(self):
        with patch.object(emb, "_configure", return_value=None):
            self.assertIsNone(emb.embed_query("q"))
            self.assertEqual(emb.embed_documents(["a", "b"]), [None, None])

    def test_documents_output_aligns_one_to_one_with_input(self):
        with patch.object(emb, "_configure", return_value=self._stub(768)):
            out = emb.embed_documents(["a", "b", "c"])
        self.assertEqual(len(out), 3)


class RateLimitBackoffTests(SimpleTestCase):
    """A 429 must back off, never fan out.

    The per-item fallback exists so one malformed chunk can't lose the other
    ninety-nine. Applied to a quota error it is catastrophic: the free tier
    allows 100 embed requests/minute, so retrying a 100-text batch item by item
    spends 100 more requests against an already-exhausted quota and loses every
    one. That is what left 491 chunks un-embedded in production.
    """

    class _Quota(Exception):
        def __str__(self):
            return "429 Resource has been exhausted (e.g. check quota)."

    def test_a_quota_error_is_recognised(self):
        self.assertTrue(emb._is_rate_limited(self._Quota()))
        self.assertTrue(emb._is_rate_limited(Exception("Quota exceeded for model")))
        self.assertTrue(emb._is_rate_limited(Exception("rate limit reached")))

    def test_an_ordinary_error_is_not_treated_as_a_quota_error(self):
        self.assertFalse(emb._is_rate_limited(Exception("invalid content part")))

    def test_a_rate_limited_batch_never_falls_back_to_per_item(self):
        """The regression: 1 refused batch must not become 100 more requests."""
        calls = []

        class _Fake:
            def embed_content(self, **kwargs):
                calls.append(kwargs["content"])
                raise RateLimitBackoffTests._Quota()

        with patch.object(emb.time, "sleep") as slept:
            out = emb._embed_batch(_Fake(), ["a", "b", "c"], "m")

        self.assertEqual(out, [None, None, None])
        # Every call carried the whole batch — none was a single string.
        self.assertTrue(all(isinstance(c, list) for c in calls))
        self.assertEqual(len(calls), emb._RATE_LIMIT_RETRIES + 1)
        self.assertEqual(slept.call_count, emb._RATE_LIMIT_RETRIES)

    def test_backoff_grows_between_retries(self):
        class _Fake:
            def embed_content(self, **kwargs):
                raise RateLimitBackoffTests._Quota()

        with patch.object(emb.time, "sleep") as slept:
            emb._embed_batch(_Fake(), ["a"], "m")
        delays = [c.args[0] for c in slept.call_args_list]
        self.assertEqual(delays, sorted(delays))
        self.assertLess(delays[0], delays[-1])

    def test_a_batch_that_recovers_after_a_pause_is_embedded(self):
        state = {"n": 0}

        class _Fake:
            def embed_content(self, **kwargs):
                state["n"] += 1
                if state["n"] == 1:
                    raise RateLimitBackoffTests._Quota()
                return {"embedding": [[0.1] * emb.EMBED_DIMENSIONS] * len(kwargs["content"])}

        with patch.object(emb.time, "sleep"):
            out = emb._embed_batch(_Fake(), ["a", "b"], "m")
        self.assertEqual(len(out), 2)
        self.assertTrue(all(v is not None for v in out))

    def test_a_non_quota_failure_still_falls_back_to_per_item(self):
        """That fallback is the whole point when one chunk is malformed."""
        seen = []

        class _Fake:
            def embed_content(self, **kwargs):
                content = kwargs["content"]
                seen.append(content)
                if isinstance(content, list):
                    raise ValueError("invalid content part")
                return {"embedding": [0.1] * emb.EMBED_DIMENSIONS}

        out = emb._embed_batch(_Fake(), ["a", "b"], "m")
        self.assertEqual(len(out), 2)
        self.assertTrue(all(v is not None for v in out))
        # One failed batch call, then one call per item.
        self.assertEqual(seen[1:], ["a", "b"])
