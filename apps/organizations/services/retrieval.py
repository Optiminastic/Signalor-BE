"""Knowledge-base retrieval / RAG (Epic 4).

Turns the stored ``BrandCorpusChunk`` embeddings (Epic 3) into query-relevant context.
``retrieve`` embeds the query, does a cosine k-NN over the org's current chunks (pgvector
HNSW index), then applies MMR so near-duplicate chunks don't crowd out coverage.
``build_knowledge_block`` renders the results into a budgeted, cited context block.

Everything is org-scoped and fail-soft: an anonymous run, an empty knowledge base, or a
failed query embedding all degrade to ``[]`` / ``""`` - never an error. The single DB
seam ``_vector_search`` (pgvector ``CosineDistance``, Postgres-only) is isolated so the
surrounding logic stays unit-testable on SQLite.

Embedding imports are function-local to keep ``analyzer -> organizations`` the only hard
dependency direction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger("apps")

_DEFAULT_K = 6
_DEFAULT_CANDIDATES = 20  # over-fetch, then MMR down to k
_MMR_LAMBDA = 0.5  # 1.0 = pure relevance, 0.0 = pure diversity
_KNOWLEDGE_HEADER = (
    "RELEVANT WEBSITE KNOWLEDGE (retrieved from the brand's own site; ground answers "
    "in these and cite the source URL when relevant):"
)
_DEFAULT_MAX_CHARS = 1500


@dataclass
class RetrievedChunk:
    text: str
    source_url: str
    heading_path: list[str] = field(default_factory=list)
    score: float = 0.0  # cosine similarity (1 - distance)
    metadata: dict = field(default_factory=dict)


# ── Public API ────────────────────────────────────────────────────────────


def retrieve(
    run_or_org,
    query: str,
    *,
    k: int = _DEFAULT_K,
    candidates: int = _DEFAULT_CANDIDATES,
) -> list[RetrievedChunk]:
    """Return up to ``k`` MMR-ranked chunks most relevant to ``query``. Never raises."""
    try:
        return _retrieve(run_or_org, query, k=k, candidates=candidates)
    except Exception:
        logger.warning("retrieve failed for query=%r", (query or "")[:80], exc_info=True)
        return []


def build_knowledge_block(
    run_or_org,
    query: str,
    *,
    k: int = _DEFAULT_K,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """Retrieve and render a budgeted, cited knowledge block for a prompt. ``""`` if empty."""
    chunks = retrieve(run_or_org, query, k=k)
    if not chunks:
        return ""
    budget = max_chars - len(_KNOWLEDGE_HEADER) - 1
    body = _render(chunks, budget)
    if not body:
        return ""
    # Hard safety cap: _render keeps at least one whole chunk, which can overflow the
    # budget if a single chunk is very large. Truncate so the block never exceeds max_chars.
    if len(body) > budget > 0:
        body = body[: budget - 1].rstrip() + "…"
    return f"{_KNOWLEDGE_HEADER}\n{body}"


# ── Internals ─────────────────────────────────────────────────────────────


def _retrieve(run_or_org, query, *, k, candidates) -> list[RetrievedChunk]:
    org = _resolve_org(run_or_org)
    if org is None or not (query or "").strip():
        return []

    from apps.analyzer.pipeline.embeddings import embed_query

    qvec = embed_query(query)
    if qvec is None:
        return []

    rows = _vector_search(org, qvec, candidates)
    ranked = _mmr(rows, k=k, lambda_=_MMR_LAMBDA)
    return [_to_chunk(chunk, rel) for chunk, _emb, rel in ranked]


def _resolve_org(run_or_org):
    from apps.organizations.models import Organization

    if isinstance(run_or_org, Organization):
        return run_or_org
    return getattr(run_or_org, "organization", None)


def _vector_search(org, qvec: list[float], limit: int):
    """DB seam (pgvector, Postgres-only): current embedded chunks nearest ``qvec``.

    Returns ``[(chunk, distance)]`` ascending by cosine distance. Mocked in unit tests
    since the ``<=>`` operator does not exist on SQLite.
    """
    from pgvector.django import CosineDistance

    from apps.organizations.models import BrandCorpusChunk

    qs = (
        BrandCorpusChunk.objects.filter(organization=org, is_current=True, embedding__isnull=False)
        .annotate(_distance=CosineDistance("embedding", qvec))
        .order_by("_distance")[:limit]
    )
    return [(c, float(c._distance)) for c in qs]


def _mmr(rows, *, k, lambda_):
    """Maximal Marginal Relevance: balance query relevance against novelty vs already-picked.

    ``rows`` is ``[(chunk, distance)]`` from the vector search (already ordered by query
    relevance); returns ``[(chunk, embedding, relevance)]`` (<= k). Relevance is the
    cosine similarity ``1 - distance``; novelty penalizes similarity to already-picked chunks.
    """
    pool = [
        (chunk, np.asarray(chunk.embedding, dtype=float), 1.0 - dist)
        for chunk, dist in rows
        if chunk.embedding is not None
    ]
    selected: list[tuple] = []
    while pool and len(selected) < k:
        best, best_score = None, None
        for item in pool:
            _chunk, emb, rel = item
            novelty_penalty = max((_cos(emb, s[1]) for s in selected), default=0.0)
            score = lambda_ * rel - (1 - lambda_) * novelty_penalty
            if best_score is None or score > best_score:
                best, best_score = item, score
        selected.append(best)
        pool.remove(best)
    return selected


def _cos(a, b) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _to_chunk(chunk, relevance: float) -> RetrievedChunk:
    return RetrievedChunk(
        text=chunk.text,
        source_url=chunk.source_url,
        heading_path=list(chunk.heading_path or []),
        score=round(float(relevance), 4),
        metadata=chunk.metadata or {},
    )


def _render(chunks: list[RetrievedChunk], max_chars: int) -> str:
    """Concatenate cited chunks until the char budget is hit (whole chunks only)."""
    parts: list[str] = []
    used = 0
    for i, ch in enumerate(chunks, start=1):
        crumb = " > ".join(ch.heading_path) if ch.heading_path else ""
        cite = " - ".join(filter(None, [crumb, ch.source_url]))
        block = f"[{i}] ({cite})\n{ch.text}" if cite else f"[{i}]\n{ch.text}"
        if used + len(block) > max_chars and parts:
            break
        parts.append(block)
        used += len(block) + 2
    return "\n\n".join(parts).strip()
