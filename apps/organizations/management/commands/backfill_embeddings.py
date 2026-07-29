"""Embed corpus chunks that were stored without a vector.

``ingest_run_pages`` stores a chunk even when embedding fails, leaving
``embedding`` null so a later run can retry. That retry only happens when the
page is re-crawled, so a period with no ``GOOGLE_API_KEY`` leaves a backlog that
never clears on its own — the chunks exist, retrieval finds nothing, and prompt
coverage reports "unknown" forever.

This drains that backlog. Idempotent: it only ever selects null-embedding rows,
so re-running after a partial or failed pass resumes rather than repeats.

    python manage.py backfill_embeddings --dry-run
    python manage.py backfill_embeddings --limit 100
    python manage.py backfill_embeddings
"""

from django.core.management.base import BaseCommand

from apps.analyzer.pipeline.embeddings import embed_documents
from apps.organizations.models import BrandCorpusChunk

# Matches the embedding client's own batch ceiling, so one command batch is one
# API round trip rather than being re-split downstream.
BATCH = 100


class Command(BaseCommand):
    help = "Generate embeddings for corpus chunks that have none."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Stop after this many chunks (0 = all). Use to bound a first run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be embedded without calling the API.",
        )
        parser.add_argument(
            "--org",
            type=int,
            default=0,
            help="Restrict to one organization id.",
        )

    def handle(self, *args, **options):
        qs = BrandCorpusChunk.objects.filter(embedding__isnull=True)
        if options["org"]:
            qs = qs.filter(organization_id=options["org"])
        # Current chunks first: superseded versions are history, not what
        # retrieval searches, so they must not consume the budget first.
        qs = qs.order_by("-is_current", "id")

        total = qs.count()
        limit = options["limit"] or total
        target = min(total, limit)

        self.stdout.write(f"Un-embedded chunks: {total}. This run will attempt: {target}.")
        if options["dry_run"]:
            by_org = {}
            for org_id in qs.values_list("organization_id", flat=True)[:target]:
                by_org[org_id] = by_org.get(org_id, 0) + 1
            for org_id, n in sorted(by_org.items()):
                self.stdout.write(f"  org {org_id}: {n} chunks")
            self.stdout.write(self.style.WARNING("Dry run — nothing called, nothing written."))
            return

        embedded, failed = self._run(qs, target)

        self.stdout.write(
            self.style.SUCCESS(f"Embedded {embedded} chunk(s).")
            if embedded
            else self.style.WARNING("Embedded 0 chunks.")
        )
        if failed:
            # Left null on purpose — they stay in the queue for the next pass
            # rather than being marked done with no vector.
            self.stdout.write(
                self.style.WARNING(f"{failed} chunk(s) could not be embedded; still queued.")
            )

    def _run(self, qs, target: int) -> tuple[int, int]:
        embedded = 0
        failed = 0
        done = 0

        while done < target:
            # Re-slice each pass: rows just written no longer match the filter,
            # so a plain offset would skip the ones shifting into its place.
            batch = list(qs[: min(BATCH, target - done)])
            if not batch:
                break

            vectors = embed_documents([c.text for c in batch])
            written = []
            for chunk, vector in zip(batch, vectors, strict=True):
                if vector is None:
                    failed += 1
                    continue
                chunk.embedding = vector
                written.append(chunk)

            if written:
                BrandCorpusChunk.objects.bulk_update(written, ["embedding"])
                embedded += len(written)

            done += len(batch)
            self.stdout.write(f"  …{embedded} embedded, {failed} failed ({done}/{target})")

            # Every item in the batch failed: the API is down or the key is bad.
            # Continuing would burn the whole backlog against the same error.
            if not written:
                self.stdout.write(self.style.ERROR("Whole batch failed — stopping."))
                break

        return embedded, failed
