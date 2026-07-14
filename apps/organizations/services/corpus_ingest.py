"""Ingest a run's crawled pages into the org knowledge base (Epic 3).

``ingest_run_pages`` is the single entry point, called fail-soft from the analyzer
orchestrator after all crawls exist. It extracts -> cleans -> chunks -> embeds ->
stores, org-scoped (anonymous runs are skipped). Storage only; retrieval is Epic 4.

Per URL it runs a content-addressed **diff-sync** against what is already stored, so
the DB converges on the page's current content without ever re-embedding unchanged
text or breaking the ``(org, source_url, content_hash)`` uniqueness:

  * unchanged chunk (hash still present)        -> left untouched (skip-unchanged)
  * removed chunk (was current, now gone)       -> soft-superseded (is_current=False)
  * new chunk (hash never seen for this URL)    -> inserted and embedded
  * returning chunk (hash superseded earlier)   -> reactivated, embedding reused

Only brand-new chunks and rows whose prior embedding failed (null) are sent to the
embedding API, and they go in a single batch. History is retained, never hard-deleted.

Analyzer/embedding imports are function-local to keep ``analyzer -> organizations``
the only hard dependency direction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.db import transaction

from .corpus_chunker import ChunkDraft, chunk_page

logger = logging.getLogger("apps")

# Cost guardrails. Bound work per run; skip-unchanged keeps steady-state cost low.
_MAX_PAGES = int(getattr(settings, "CORPUS_MAX_PAGES", 25))
_MAX_NEW_CHUNKS = int(getattr(settings, "CORPUS_MAX_CHUNKS_PER_RUN", 300))


@dataclass
class IngestStats:
    pages: int = 0
    chunks_created: int = 0
    chunks_reactivated: int = 0
    chunks_superseded: int = 0
    chunks_reused: int = 0
    chunks_embedded: int = 0
    urls_changed: int = 0
    dropped_for_cap: int = 0
    skipped: bool = False


@dataclass
class _UrlPlan:
    """Pending DB mutations for one URL (nothing is written until commit)."""

    new_rows: list = field(default_factory=list)  # unsaved BrandCorpusChunk
    reactivate: list = field(default_factory=list)  # existing chunks -> current again
    retry_embed: list = field(default_factory=list)  # existing chunks with null embedding
    supersede_ids: list = field(default_factory=list)
    reused: int = 0
    changed: bool = False


def ingest_run_pages(run, pages: list[dict]) -> IngestStats:
    """Ingest ``pages`` (each ``{"url","html","text"}``) for ``run.organization``.

    Returns stats; ``skipped=True`` for anonymous runs or when disabled. Never raises.
    """
    try:
        return _ingest(run, pages)
    except Exception:
        logger.warning("corpus ingestion failed for run=%s", getattr(run, "pk", "?"), exc_info=True)
        return IngestStats(skipped=True)


def _norm_url(url: str) -> str:
    """Canonicalize for stable matching: drop fragment/query, trailing slash."""
    parts = urlsplit((url or "").strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _ingest(run, pages: list[dict]) -> IngestStats:

    org = getattr(run, "organization", None)
    if org is None:
        return IngestStats(skipped=True)
    if not getattr(settings, "SIGNALOR_ENABLE_INGESTION", True):
        return IngestStats(skipped=True)

    stats = IngestStats()
    plans: list[_UrlPlan] = []
    budget = _MAX_NEW_CHUNKS

    for page in pages[:_MAX_PAGES]:
        url = _norm_url(page.get("url", ""))
        html = page.get("html") or ""
        if not url or not html:
            continue
        stats.pages += 1
        drafts = _dedupe(chunk_page(html, url=url))
        plan, budget = _plan_url(org, run, url, drafts, budget, stats)
        plans.append(plan)

    _embed_pending(plans)
    _commit(plans, stats)
    _log_summary(run, stats)
    return stats


def _dedupe(drafts: list[ChunkDraft]) -> list[ChunkDraft]:
    """Collapse chunks that hash identically within a single page (repeated text)."""
    seen: set[str] = set()
    out: list[ChunkDraft] = []
    for d in drafts:
        if d.content_hash in seen:
            continue
        seen.add(d.content_hash)
        out.append(d)
    return out


def _plan_url(org, run, url, drafts, budget, stats) -> tuple[_UrlPlan, int]:
    """Diff ``drafts`` against stored chunks for this URL and build the mutation plan."""
    from apps.organizations.models import BrandCorpusChunk

    existing = list(BrandCorpusChunk.objects.filter(organization=org, source_url=url))
    by_hash = {c.content_hash: c for c in existing}
    current_hashes = {c.content_hash for c in existing if c.is_current}
    new_by_hash = {d.content_hash: d for d in drafts}
    new_hashes = set(new_by_hash)

    plan = _UrlPlan()

    if new_hashes == current_hashes:
        # Page unchanged: retry only chunks whose embedding failed previously.
        for h in new_hashes:
            chunk = by_hash[h]
            if chunk.embedding is None:
                plan.retry_embed.append(chunk)
            else:
                plan.reused += 1
        stats.chunks_reused += plan.reused
        return plan, budget

    plan.changed = True
    stats.urls_changed += 1
    rev = max((c.version for c in existing), default=0) + 1

    # Removed chunks -> soft-supersede.
    plan.supersede_ids = [c.id for c in existing if c.is_current and c.content_hash not in new_hashes]

    for h, draft in new_by_hash.items():
        prior = by_hash.get(h)
        if prior is not None and prior.is_current:
            plan.reused += 1  # unchanged, still current
            continue
        if prior is not None:  # returning content -> reactivate the old row
            prior.is_current = True
            prior.version = rev
            plan.reactivate.append(prior)
            if prior.embedding is None:
                plan.retry_embed.append(prior)
            continue
        if budget <= 0:  # brand-new content, but out of per-run budget
            stats.dropped_for_cap += 1
            continue
        budget -= 1
        plan.new_rows.append(_build_row(org, run, url, draft, rev))

    stats.chunks_reused += plan.reused
    return plan, budget


def _build_row(org, run, url, draft: ChunkDraft, version: int):
    from apps.organizations.models import BrandCorpusChunk

    return BrandCorpusChunk(
        organization=org,
        source_run=run,
        source_url=url,
        heading_path=draft.heading_path,
        text=draft.text,
        metadata=draft.metadata,
        content_hash=draft.content_hash,
        embedding=None,
        embedding_model="",
        version=version,
        is_current=True,
    )


def _embed_pending(plans: list[_UrlPlan]) -> None:
    """Batch-embed every row across all plans that still lacks an embedding."""
    from apps.analyzer.pipeline.embeddings import DEFAULT_EMBED_MODEL, embed_documents

    targets = [row for p in plans for row in (*p.new_rows, *p.retry_embed) if row.embedding is None]
    if not targets:
        return
    vectors = embed_documents([row.text for row in targets])
    for row, vector in zip(targets, vectors, strict=True):
        if vector is not None:
            row.embedding = vector
            row.embedding_model = DEFAULT_EMBED_MODEL


def _commit(plans: list[_UrlPlan], stats: IngestStats) -> None:
    from apps.organizations.models import BrandCorpusChunk

    new_rows = [row for p in plans for row in p.new_rows]
    supersede_ids = [cid for p in plans for cid in p.supersede_ids]
    reactivate = [c for p in plans for c in p.reactivate]
    retry = [c for p in plans for c in p.retry_embed]

    with transaction.atomic():
        if supersede_ids:
            BrandCorpusChunk.objects.filter(id__in=supersede_ids).update(is_current=False)
        for chunk in reactivate:
            chunk.save(update_fields=["is_current", "version", "embedding", "embedding_model", "updated_at"])
        for chunk in retry:
            if chunk not in reactivate:
                chunk.save(update_fields=["embedding", "embedding_model", "updated_at"])
        if new_rows:
            BrandCorpusChunk.objects.bulk_create(new_rows)

    stats.chunks_created = len(new_rows)
    stats.chunks_reactivated = len(reactivate)
    stats.chunks_superseded = len(supersede_ids)
    stats.chunks_embedded = sum(1 for r in (*new_rows, *retry) if r.embedding is not None)


def _log_summary(run, stats: IngestStats) -> None:
    logger.info(
        "corpus ingest run=%s: pages=%s created=%s reactivated=%s superseded=%s "
        "reused=%s embedded=%s dropped=%s",
        getattr(run, "pk", "?"),
        stats.pages,
        stats.chunks_created,
        stats.chunks_reactivated,
        stats.chunks_superseded,
        stats.chunks_reused,
        stats.chunks_embedded,
        stats.dropped_for_cap,
    )
    if stats.dropped_for_cap:
        logger.warning(
            "corpus ingest run=%s hit the per-run chunk cap (%s); %s new chunks dropped",
            getattr(run, "pk", "?"),
            _MAX_NEW_CHUNKS,
            stats.dropped_for_cap,
        )
