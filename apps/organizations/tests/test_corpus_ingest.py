"""Tests for the knowledge-base ingestion service (Epic 3). Embeddings are mocked."""

from unittest.mock import patch

from django.test import TestCase

from apps.analyzer.models import AnalysisRun
from apps.organizations.models import BrandCorpusChunk, Organization
from apps.organizations.services import corpus_ingest

_EMBED = "apps.analyzer.pipeline.embeddings.embed_documents"

PAGE_V1 = """
<body>
  <h1>Acme Widgets</h1>
  <p>Acme builds durable widgets for industrial teams across the whole world.</p>
  <h2>Pricing</h2>
  <p>Plans start at forty nine dollars per month when billed annually upfront.</p>
</body>
"""
# Only the second paragraph differs from V1.
PAGE_V2 = PAGE_V1.replace("forty nine", "fifty nine")


def _real_vectors(texts):
    return [[0.1] * 768 for _ in texts]


def _page(html, url="https://acme.com"):
    return {"url": url, "html": html, "text": ""}


class IngestTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", url="https://acme.com", owner_email="o@x.com")
        self.run = AnalysisRun.objects.create(
            url="https://acme.com", brand_name="Acme", organization=self.org
        )

    def test_anonymous_run_is_skipped(self):
        run_no_org = AnalysisRun.objects.create(url="https://x.com")
        with patch(_EMBED, side_effect=_real_vectors) as mock_embed:
            stats = corpus_ingest.ingest_run_pages(run_no_org, [_page(PAGE_V1)])
        self.assertTrue(stats.skipped)
        self.assertEqual(BrandCorpusChunk.objects.count(), 0)
        mock_embed.assert_not_called()

    def test_creates_and_embeds_chunks(self):
        with patch(_EMBED, side_effect=_real_vectors) as mock_embed:
            stats = corpus_ingest.ingest_run_pages(self.run, [_page(PAGE_V1)])
        self.assertEqual(stats.chunks_created, 2)
        self.assertEqual(mock_embed.call_count, 1)
        rows = BrandCorpusChunk.objects.filter(organization=self.org)
        self.assertEqual(rows.count(), 2)
        self.assertTrue(all(r.is_current and r.embedding_model for r in rows))
        self.assertTrue(all(r.source_run_id == self.run.id for r in rows))

    def test_unchanged_page_skips_reembedding(self):
        with patch(_EMBED, side_effect=_real_vectors):
            corpus_ingest.ingest_run_pages(self.run, [_page(PAGE_V1)])
        with patch(_EMBED, side_effect=_real_vectors) as mock_embed:
            stats = corpus_ingest.ingest_run_pages(self.run, [_page(PAGE_V1)])
        self.assertEqual(stats.chunks_created, 0)
        self.assertEqual(stats.chunks_reused, 2)
        mock_embed.assert_not_called()
        self.assertEqual(BrandCorpusChunk.objects.count(), 2)

    def test_changed_page_versions_and_supersedes(self):
        with patch(_EMBED, side_effect=_real_vectors):
            corpus_ingest.ingest_run_pages(self.run, [_page(PAGE_V1)])
        with patch(_EMBED, side_effect=_real_vectors) as mock_embed:
            stats = corpus_ingest.ingest_run_pages(self.run, [_page(PAGE_V2)])
        self.assertEqual(stats.chunks_created, 1)  # only the changed paragraph
        self.assertEqual(mock_embed.call_args.args[0].__len__(), 1)
        self.assertEqual(BrandCorpusChunk.objects.filter(is_current=True).count(), 2)
        self.assertEqual(BrandCorpusChunk.objects.filter(is_current=False).count(), 1)
        self.assertEqual(BrandCorpusChunk.objects.filter(is_current=True, version=2).count(), 1)

    def test_failed_embedding_is_retried_next_run(self):
        state = {"first": True}

        def flaky(texts):
            if state["first"]:
                state["first"] = False
                return [None] * len(texts)
            return _real_vectors(texts)

        with patch(_EMBED, side_effect=flaky) as mock_embed:
            corpus_ingest.ingest_run_pages(self.run, [_page(PAGE_V1)])
            self.assertTrue(all(r.embedding is None for r in BrandCorpusChunk.objects.all()))
            stats2 = corpus_ingest.ingest_run_pages(self.run, [_page(PAGE_V1)])

        self.assertEqual(stats2.chunks_created, 0)
        self.assertEqual(stats2.chunks_embedded, 2)
        self.assertEqual(mock_embed.call_count, 2)
        self.assertTrue(all(r.embedding is not None for r in BrandCorpusChunk.objects.all()))

    @patch.object(corpus_ingest, "_MAX_NEW_CHUNKS", 1)
    def test_per_run_chunk_cap_drops_and_logs(self):
        with patch(_EMBED, side_effect=_real_vectors) as mock_embed:
            stats = corpus_ingest.ingest_run_pages(self.run, [_page(PAGE_V1)])
        self.assertEqual(stats.chunks_created, 1)
        self.assertGreaterEqual(stats.dropped_for_cap, 1)
        self.assertEqual(mock_embed.call_args.args[0].__len__(), 1)
