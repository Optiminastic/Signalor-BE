"""
Content-addressed cache for expensive LLM analysis results.

The Anthropic *prompt* cache in ``llm.py`` bills repeated input tokens cheaply, but
it does not skip the call. This layer skips the call entirely when the exact same
analysis has already been produced for the exact same inputs.

Correctness first — the cache is **content-addressed, not similarity-addressed**.
The key is a hash of ``(feature, prompt_version, tier, fingerprint)`` where
``fingerprint`` is the caller's stable identity of the source material (e.g.
``org_id:url:content_hash``). Two pages that merely *look* similar get different
keys; a page whose content changed gets a new ``content_hash`` and therefore a new
key. A fuzzy embedding-similarity cache would risk returning a stale analysis when
the page changed but the query looked alike — this design cannot.

Invalidation is by construction: bump ``prompt_version`` to retire every entry for a
feature, or let the content_hash change roll the key. Entries also carry a TTL.

Reuses :func:`apps.analyzer._cache.cached_or_compute` for the backend (LocMem in dev,
Redis in prod) — no new cache plumbing. Off by default (opt-in via env).
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Callable

from .._cache import cached_or_compute

logger = logging.getLogger("apps")

_KEY_PREFIX = "llmresp"
# 7 days. LLM analysis of a fixed page+prompt is stable; the content_hash in the
# fingerprint is what actually forces a refresh, so a generous TTL is safe.
DEFAULT_TTL_SECONDS = 7 * 24 * 3600


def _enabled() -> bool:
    """Opt-in kill-switch. Off by default so enabling the cache is a deliberate act
    (it changes when the model is and isn't called)."""
    return os.getenv("LLM_RESPONSE_CACHE_ENABLED", "false").strip().lower() == "true"


def build_fingerprint(*parts: Any) -> str:
    """Join stable identity parts into one fingerprint string.

    Pass the things that, if any changes, should invalidate the cached result —
    typically ``organization_id``, ``source_url`` and the page ``content_hash``.
    ``None`` parts are rendered as empty so an omitted part is stable, not random.
    """
    return "|".join("" if p is None else str(p) for p in parts)


def response_cache_key(*, feature: str, prompt_version: str | int, tier: str, fingerprint: str) -> str:
    """Deterministic cache key for one analysis result.

    ``feature``        logical call site, e.g. "competitors" / "eeat".
    ``prompt_version`` bump to invalidate every entry for the feature at once.
    ``tier``           model tier, so a cheap-tier result never masquerades as strong.
    ``fingerprint``    caller's content identity (see :func:`build_fingerprint`).
    """
    raw = "|".join([feature.strip(), str(prompt_version), tier.strip(), fingerprint])
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()
    return f"{_KEY_PREFIX}:{feature.strip()}:{digest}"


def cached_llm(
    *,
    feature: str,
    prompt_version: str | int,
    tier: str,
    fingerprint: str,
    compute: Callable[[], Any],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Any:
    """Return a cached analysis result for these inputs, else compute, store, return.

    When the cache is disabled (default) this is a transparent pass-through to
    ``compute()`` — same return value, no caching, so wiring it in is always safe.
    ``None`` results are treated as a miss and are never cached (a failed analysis
    must be retried, not memoized).
    """
    if not _enabled():
        return compute()
    key = response_cache_key(
        feature=feature, prompt_version=prompt_version, tier=tier, fingerprint=fingerprint
    )
    return cached_or_compute(key, ttl_seconds, compute)
