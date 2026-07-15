"""Semantic response cache for repeat LLM prompts (Epic 7).

Two-tier lookup, both constrained to the same scope:

  1. **Exact hash** - sha256 of the normalized prompt. Free, zero-risk, no embedding call.
  2. **Semantic** - cosine k-NN over the prompt embedding, accepted only above a
     conservative similarity floor (``SIMILARITY_FLOOR``).

The scope ``(purpose, model_key, organization)`` is the safety property: a lookup can
only ever match an entry stored under the identical scope, so two brands can never share
a cached response. Everything is fail-soft - a cache error just means a normal LLM call.

Opt-in per call site (``ask_llm(cache=True)``) and killable via
``SIGNALOR_ENABLE_SEMANTIC_CACHE``.
"""

from __future__ import annotations

import hashlib
import logging
import re

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("apps")

# Only near-identical prompts may hit the semantic tier. Cosine similarity in [0, 1].
SIMILARITY_FLOOR = float(getattr(settings, "SEMANTIC_CACHE_SIMILARITY", 0.97))
# How long a cached response stays valid.
TTL_SECONDS = int(getattr(settings, "SEMANTIC_CACHE_TTL_SECONDS", 7 * 24 * 3600))
# Prompt preview retained for debugging.
_PREVIEW_CHARS = 500
_WS_RE = re.compile(r"\s+")


def is_enabled() -> bool:
    return bool(getattr(settings, "SIGNALOR_ENABLE_SEMANTIC_CACHE", True))


def _normalize(prompt: str) -> str:
    return _WS_RE.sub(" ", (prompt or "")).strip()


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(_normalize(prompt).encode("utf-8")).hexdigest()


def _scoped(purpose: str, model_key: str, org):
    from ..models import LLMResponseCache

    return LLMResponseCache.objects.filter(
        purpose=purpose,
        model_key=model_key,
        organization=org,
        expires_at__gt=timezone.now(),
    )


def lookup(prompt: str, *, purpose: str, model_key: str, org=None) -> str | None:
    """Return a cached response for ``prompt`` within this scope, else ``None``.

    Never raises: any cache failure degrades to a normal LLM call.
    """
    if not is_enabled() or not (prompt or "").strip():
        return None
    try:
        return _lookup(prompt, purpose=purpose, model_key=model_key, org=org)
    except Exception:
        logger.warning("response_cache lookup failed (purpose=%s)", purpose, exc_info=True)
        return None


def _lookup(prompt, *, purpose, model_key, org) -> str | None:
    # 1. Exact hash -- free and cannot mismatch.
    exact = _scoped(purpose, model_key, org).filter(prompt_hash=prompt_hash(prompt)).first()
    if exact is not None:
        _bump(exact)
        logger.info("response_cache HIT (exact) purpose=%s", purpose)
        return exact.response_text

    # 2. Semantic -- near-identical prompts only.
    hit, similarity = _semantic_search(prompt, purpose=purpose, model_key=model_key, org=org)
    if hit is not None and similarity >= SIMILARITY_FLOOR:
        _bump(hit)
        logger.info("response_cache HIT (semantic %.4f) purpose=%s", similarity, purpose)
        return hit.response_text
    return None


def _semantic_search(prompt, *, purpose, model_key, org):
    """DB seam (pgvector, Postgres-only): nearest scoped entry + its cosine similarity.

    Mocked in unit tests since the ``<=>`` operator does not exist on SQLite.
    """
    from pgvector.django import CosineDistance

    from .embeddings import embed_query

    qvec = embed_query(prompt)
    if qvec is None:
        return None, 0.0
    row = (
        _scoped(purpose, model_key, org)
        .filter(prompt_embedding__isnull=False)
        .annotate(_distance=CosineDistance("prompt_embedding", qvec))
        .order_by("_distance")
        .first()
    )
    if row is None:
        return None, 0.0
    return row, 1.0 - float(row._distance)


def _bump(entry) -> None:
    from django.db.models import F

    from ..models import LLMResponseCache

    LLMResponseCache.objects.filter(pk=entry.pk).update(hit_count=F("hit_count") + 1)


def store(prompt: str, response: str, *, purpose: str, model_key: str, org=None) -> None:
    """Cache ``response`` for ``prompt`` in this scope. Never raises."""
    if not is_enabled() or not (prompt or "").strip() or not (response or "").strip():
        return
    try:
        _store(prompt, response, purpose=purpose, model_key=model_key, org=org)
    except Exception:
        logger.warning("response_cache store failed (purpose=%s)", purpose, exc_info=True)


def _store(prompt, response, *, purpose, model_key, org) -> None:
    from datetime import timedelta

    from ..models import LLMResponseCache
    from .embeddings import embed_query

    LLMResponseCache.objects.update_or_create(
        purpose=purpose,
        model_key=model_key,
        organization=org,
        prompt_hash=prompt_hash(prompt),
        defaults={
            "prompt_text": _normalize(prompt)[:_PREVIEW_CHARS],
            "prompt_embedding": embed_query(prompt),
            "response_text": response,
            "expires_at": timezone.now() + timedelta(seconds=TTL_SECONDS),
        },
    )
