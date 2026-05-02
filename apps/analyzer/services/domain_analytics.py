"""
Domain Analytics service — SEMrush-style "real-world signals" sourced from
DataForSEO Labs. Generates estimated organic traffic, top ranking keywords,
and top traffic-driving pages for the run's domain WITHOUT requiring the
user to connect Google Analytics.

Cached per workspace for 7 days. The numbers are SERP-derived estimates
(rank x search volume x CTR), not real sessions, so a long TTL is fine.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from django.utils import timezone

from apps.analyzer.models import AnalysisRun
from apps.integrations.services.dataforseo import (
    DataForSEOError,
    DataForSEONotConfigured,
    fetch_domain_geo_distribution,
    fetch_domain_overview,
    fetch_ranked_keywords,
    fetch_relevant_pages,
)

logger = logging.getLogger("apps")

CACHE_TTL = timedelta(days=7)
KEYWORDS_LIMIT = 50
PAGES_LIMIT = 20


class DomainAnalyticsError(Exception):
    """Raised when the snapshot can't be generated and no cached one exists."""


def _domain_from_run(run: AnalysisRun) -> str:
    url = (run.url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    return urlparse(url).netloc or url


def get_or_generate(run: AnalysisRun, *, force: bool = False) -> dict[str, Any]:
    """
    Return the cached snapshot or fetch a fresh one. Raises
    ``DomainAnalyticsError`` if fetching fails and no cached snapshot exists.
    """
    from apps.analyzer.models import DomainAnalyticsSnapshot

    existing = DomainAnalyticsSnapshot.objects.filter(analysis_run=run).first()
    if not force and existing and _is_fresh(existing):
        return _serialize(existing, cached=True)

    domain = _domain_from_run(run)
    if not domain:
        raise DomainAnalyticsError("Run has no URL — cannot fetch domain analytics.")

    try:
        overview = fetch_domain_overview(domain)
        keywords = fetch_ranked_keywords(domain, limit=KEYWORDS_LIMIT)
        pages = fetch_relevant_pages(domain, limit=PAGES_LIMIT)
        geo = fetch_domain_geo_distribution(domain)
    except DataForSEONotConfigured:
        if existing:
            return _serialize(existing, cached=True)
        raise
    except DataForSEOError as exc:
        logger.warning("domain_analytics fetch failed for %s: %s", domain, exc)
        if existing:
            return _serialize(existing, cached=True)
        raise DomainAnalyticsError(str(exc)) from exc

    snapshot, _ = DomainAnalyticsSnapshot.objects.update_or_create(
        analysis_run=run,
        defaults={
            "overview": overview or {},
            "top_keywords": keywords or [],
            "top_pages": pages or [],
            "geo_distribution": geo or {},
        },
    )
    return _serialize(snapshot, cached=False)


def invalidate(slug: str) -> None:
    if not slug:
        return
    from apps.analyzer.models import DomainAnalyticsSnapshot
    try:
        DomainAnalyticsSnapshot.objects.filter(analysis_run__slug=slug).delete()
    except Exception:
        logger.warning("domain_analytics invalidate failed for %s", slug, exc_info=True)


def _is_fresh(snapshot) -> bool:
    if not snapshot.synced_at:
        return False
    return timezone.now() - snapshot.synced_at < CACHE_TTL


def _serialize(snapshot, *, cached: bool) -> dict[str, Any]:
    return {
        "domain": _domain_from_run(snapshot.analysis_run),
        "overview": snapshot.overview or {},
        "top_keywords": snapshot.top_keywords or [],
        "top_pages": snapshot.top_pages or [],
        "geo_distribution": snapshot.geo_distribution or {},
        "synced_at": snapshot.synced_at.isoformat() if snapshot.synced_at else None,
        "cached": cached,
    }
