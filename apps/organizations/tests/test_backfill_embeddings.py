"""Tests for the corpus embedding backfill.

Chunks are stored even when embedding fails, so a spell without a
``GOOGLE_API_KEY`` leaves rows that retrieval can never match and that nothing
retries until the page is re-crawled. This command drains that backlog, and the
contracts that matter are: it only touches un-embedded rows, a failure leaves the
row queued rather than marked done, and a total outage stops instead of burning
the whole backlog against the same error.
"""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.organizations.models import BrandCorpusChunk, Organization

_EMBED = "apps.organizations.management.commands.backfill_embeddings.embed_documents"
DIMS = 768


def _vector(seed: float = 0.1) -> list[float]:
    return [seed] * DIMS


class BackfillEmbeddingsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", owner_email="o@acme.com")

    def _chunk(self, text="body", *, embedding=None, current=True) -> BrandCorpusChunk:
        return BrandCorpusChunk.objects.create(
            organization=self.org,
            source_url="https://acme.com/x",
            text=text,
            content_hash=f"h{text}{current}{BrandCorpusChunk.objects.count()}",
            embedding=embedding,
            is_current=current,
        )

    def _run(self, **kwargs) -> str:
        out = StringIO()
        call_command("backfill_embeddings", stdout=out, **kwargs)
        return out.getvalue()

    def test_an_unembedded_chunk_gets_a_vector(self):
        chunk = self._chunk()
        with patch(_EMBED, return_value=[_vector()]):
            self._run()
        chunk.refresh_from_db()
        self.assertEqual(len(chunk.embedding), DIMS)

    def test_an_already_embedded_chunk_is_not_re_embedded(self):
        """Re-embedding is billed work that changes nothing."""
        self._chunk(embedding=_vector(0.5))
        with patch(_EMBED, side_effect=AssertionError("must not call the API")) as embed:
            self._run()
        embed.assert_not_called()

    def test_a_failed_chunk_stays_queued_rather_than_marked_done(self):
        chunk = self._chunk()
        with patch(_EMBED, return_value=[None]):
            output = self._run()
        chunk.refresh_from_db()
        self.assertIsNone(chunk.embedding)
        self.assertIn("still queued", output)

    def test_a_totally_failing_batch_stops_instead_of_draining_the_backlog(self):
        """A bad key would otherwise burn every remaining chunk against the same error."""
        for i in range(5):
            self._chunk(f"t{i}")
        with patch(_EMBED, return_value=[None] * 5) as embed:
            output = self._run()
        self.assertEqual(embed.call_count, 1)
        self.assertIn("stopping", output.lower())

    def test_partial_failure_writes_the_successes(self):
        a, b = self._chunk("a"), self._chunk("b")
        with patch(_EMBED, return_value=[_vector(), None]):
            self._run()
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertIsNotNone(a.embedding)
        self.assertIsNone(b.embedding)

    def test_dry_run_calls_nothing_and_writes_nothing(self):
        chunk = self._chunk()
        with patch(_EMBED, side_effect=AssertionError("must not call the API")):
            output = self._run(dry_run=True)
        chunk.refresh_from_db()
        self.assertIsNone(chunk.embedding)
        self.assertIn("Dry run", output)

    def test_limit_bounds_the_run(self):
        for i in range(4):
            self._chunk(f"t{i}")
        with patch(_EMBED, return_value=[_vector()] * 2):
            self._run(limit=2)
        self.assertEqual(BrandCorpusChunk.objects.exclude(embedding=None).count(), 2)

    def test_org_filter_leaves_other_orgs_alone(self):
        other = Organization.objects.create(name="Other", owner_email="x@x.com")
        theirs = BrandCorpusChunk.objects.create(
            organization=other, source_url="https://o.com", text="t", content_hash="zz"
        )
        self._chunk()
        with patch(_EMBED, return_value=[_vector()]):
            self._run(org=self.org.id)
        theirs.refresh_from_db()
        self.assertIsNone(theirs.embedding)

    def test_current_chunks_are_embedded_before_superseded_history(self):
        """Superseded versions are history; retrieval only searches current rows."""
        old = self._chunk("old", current=False)
        new = self._chunk("new", current=True)
        with patch(_EMBED, return_value=[_vector()]) as embed:
            self._run(limit=1)
        old.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual(embed.call_args.args[0], ["new"])
        self.assertIsNotNone(new.embedding)
        self.assertIsNone(old.embedding)

    def test_an_empty_backlog_is_a_no_op(self):
        with patch(_EMBED, side_effect=AssertionError("must not call the API")):
            output = self._run()
        self.assertIn("Un-embedded chunks: 0", output)
