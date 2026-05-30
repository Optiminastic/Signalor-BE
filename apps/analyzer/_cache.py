"""
Light caching helpers for read-heavy endpoints whose underlying data changes
infrequently (citation roll-ups, share-of-voice, score history, etc.).

Cache backend is Django's configured cache — LocMemCache in dev (per-process,
fine) or Redis in prod (via django-redis, already in requirements.txt).

All invalidation helpers are **best-effort**: they catch and log any cache
backend error internally so call sites stay clean (no defensive try/except
wrappers needed). A failed invalidation is never an excuse to fail the
underlying business operation.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from django.core.cache import cache

logger = logging.getLogger("apps")


def cached_or_compute(key: str, ttl_seconds: int, compute: Callable[[], Any]) -> Any:
    """
    Return cached value for ``key`` if present, otherwise compute it, store, and return.

    Treat None as a cache miss so callers can short-circuit "no data" cases.
    Cache backend errors are logged but never raised — we'd rather miss a
    cache hit than fail the request.
    """
    try:
        hit = cache.get(key)
    except Exception:
        logger.warning("cache.get(%r) failed", key, exc_info=True)
        hit = None
    if hit is not None:
        return hit
    value = compute()
    if value is not None:
        try:
            cache.set(key, value, ttl_seconds)
        except Exception:
            logger.warning("cache.set(%r) failed", key, exc_info=True)
    return value


def invalidate_run_aggregates(slug: str) -> None:
    """
    Drop every cache entry that depends on a single run's data.

    Call this when:
      - a prompt is rechecked (new results / citations)
      - an analysis run completes
      - a prompt is added / deleted

    Best-effort: silent on cache backend failure.
    """
    if not slug:
        return
    keys = [f"sov:{slug}", f"cite:{slug}", f"trend:{slug}"]
    try:
        cache.delete_many(keys)
    except Exception:
        logger.warning("cache.delete_many for slug=%s failed", slug, exc_info=True)


def invalidate_email_aggregates(email: str) -> None:
    """Drop caches keyed by user email (currently only score history)."""
    if not email:
        return
    try:
        cache.delete(f"hist:{email.strip().lower()}")
    except Exception:
        logger.warning("cache.delete for email failed", exc_info=True)
