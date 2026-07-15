"""Tests for knowledge-base retrieval / RAG (Epic 4).

The pgvector ``CosineDistance`` query (``_vector_search``) is Postgres-only, so it is
mocked here; these tests cover org resolution, MMR ranking/de-dup, mapping, budgeting
and fail-soft behavior on SQLite. The real pgvector path is verified by a live Neon smoke.
"""

from unittest.mock import patch

from django.test import TestCase

from apps.analyzer.models import AnalysisRun
from apps.organizations.models import BrandCorpusChunk, Organization
from apps.organizations.services import retrieval

_EMBED_Q = "apps.analyzer.pipeline.embeddings.embed_query"
_SEARCH = "apps.organizations.services.retrieval._vector_search"


def _chunk(org, run, *, text, url="https://acme.com/p", heading=None, embedding=(1.0, 0.0, 0.0)):
    return BrandCorpusChunk(
        organization=org,
        source_run=run,
        source_url=url,
        heading_path=heading or [],
        text=text,
        metadata={},
        content_hash=text[:16],
        embedding=list(embedding),
    )


class RetrieveTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", url="https://acme.com", owner_email="o@x.com")
        self.run = AnalysisRun.objects.create(
            url="https://acme.com", brand_name="Acme", organization=self.org
        )

    def test_anonymous_run_returns_empty(self):
        run_no_org = AnalysisRun.objects.create(url="https://x.com")
        with patch(_EMBED_Q) as mq, patch(_SEARCH) as ms:
            self.assertEqual(retrieval.retrieve(run_no_org, "anything"), [])
        mq.assert_not_called()
        ms.assert_not_called()

    def test_blank_query_returns_empty(self):
        with patch(_EMBED_Q) as mq:
            self.assertEqual(retrieval.retrieve(self.run, "   "), [])
        mq.assert_not_called()

    def test_failed_query_embedding_returns_empty(self):
        with patch(_EMBED_Q, return_value=None), patch(_SEARCH) as ms:
            self.assertEqual(retrieval.retrieve(self.run, "pricing"), [])
        ms.assert_not_called()

    def test_returns_relevance_ranked_chunks(self):
        rows = [
            (_chunk(self.org, self.run, text="Enterprise pricing plans", embedding=(1, 0, 0)), 0.05),
            (_chunk(self.org, self.run, text="About the team", embedding=(0, 1, 0)), 0.40),
        ]
        with patch(_EMBED_Q, return_value=[1.0, 0.0, 0.0]), patch(_SEARCH, return_value=rows):
            out = retrieval.retrieve(self.run, "pricing")
        self.assertEqual([c.text for c in out], ["Enterprise pricing plans", "About the team"])
        self.assertAlmostEqual(out[0].score, 0.95, places=4)  # 1 - distance

    def test_mmr_deduplicates_near_identical_chunks(self):
        # Two nearly identical top hits + one diverse chunk; MMR should surface the
        # diverse one at rank 2 instead of the redundant near-duplicate.
        rows = [
            (_chunk(self.org, self.run, text="pricing A", embedding=(1.0, 0.0, 0.0)), 0.02),
            (_chunk(self.org, self.run, text="pricing A dup", embedding=(0.99, 0.01, 0.0)), 0.03),
            (_chunk(self.org, self.run, text="shipping policy", embedding=(0.0, 0.0, 1.0)), 0.20),
        ]
        with patch(_EMBED_Q, return_value=[1.0, 0.0, 0.0]), patch(_SEARCH, return_value=rows):
            out = retrieval.retrieve(self.run, "pricing", k=2)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].text, "pricing A")
        self.assertEqual(out[1].text, "shipping policy")

    def test_k_limits_result_count(self):
        rows = [
            (_chunk(self.org, self.run, text=f"chunk {i}", embedding=(i + 1, 1, 0)), 0.1 * i)
            for i in range(6)
        ]
        with patch(_EMBED_Q, return_value=[1.0, 0.0, 0.0]), patch(_SEARCH, return_value=rows):
            out = retrieval.retrieve(self.run, "q", k=3)
        self.assertEqual(len(out), 3)

    def test_fail_soft_on_search_error(self):
        with patch(_EMBED_Q, return_value=[1.0, 0.0, 0.0]), patch(_SEARCH, side_effect=RuntimeError):
            self.assertEqual(retrieval.retrieve(self.run, "pricing"), [])


class KnowledgeBlockTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", url="https://acme.com", owner_email="o@x.com")
        self.run = AnalysisRun.objects.create(url="https://acme.com", organization=self.org)

    def test_empty_when_no_results(self):
        with patch.object(retrieval, "retrieve", return_value=[]):
            self.assertEqual(retrieval.build_knowledge_block(self.run, "pricing"), "")

    def test_renders_header_and_citations(self):
        results = [
            retrieval.RetrievedChunk(
                text="Plans start at 49 dollars.",
                source_url="https://acme.com/pricing",
                heading_path=["Pricing", "Plans"],
                score=0.95,
            )
        ]
        with patch.object(retrieval, "retrieve", return_value=results):
            block = retrieval.build_knowledge_block(self.run, "pricing")
        self.assertIn("RELEVANT WEBSITE KNOWLEDGE", block)
        self.assertIn("Pricing > Plans - https://acme.com/pricing", block)
        self.assertIn("Plans start at 49 dollars.", block)

    def test_respects_char_budget(self):
        # Header (~130) + two ~150-char chunks fit under 500; the third is dropped.
        results = [
            retrieval.RetrievedChunk(text="A" * 150, source_url="https://acme.com/a"),
            retrieval.RetrievedChunk(text="B" * 150, source_url="https://acme.com/b"),
            retrieval.RetrievedChunk(text="C" * 150, source_url="https://acme.com/c"),
        ]
        with patch.object(retrieval, "retrieve", return_value=results):
            block = retrieval.build_knowledge_block(self.run, "q", max_chars=500)
        self.assertLessEqual(len(block), 500)
        self.assertIn("A" * 150, block)  # first whole chunk kept
        self.assertNotIn("C" * 150, block)  # last chunk dropped by budget

    def test_single_oversized_chunk_is_hard_capped(self):
        results = [retrieval.RetrievedChunk(text="A" * 4000, source_url="https://acme.com/a")]
        with patch.object(retrieval, "retrieve", return_value=results):
            block = retrieval.build_knowledge_block(self.run, "q", max_chars=500)
        self.assertLessEqual(len(block), 500)
