"""
Free Domain Rating tool service.

Public, no-auth domain authority lookup powering ``/api/analyzer/tools/domain-rating/``.
For any domain a visitor enters, return a familiar 0-100 Domain Rating plus the
domain's global rank, sourced from the free Open PageRank API (Common Crawl data).

SOLID notes:
  * SRP — one job: validate a domain + compose its authority payload.
  * Dependency Inversion — depends on the small ``openpagerank`` fetcher API,
    not the HTTP client. Swap providers by changing only that fetcher.

Results are cached per-domain (``_cache.cached_or_compute``); the underlying
Open PageRank data refreshes roughly monthly, so a 7-day TTL is plenty.
"""
from __future__ import annotations

import re

from django.utils import timezone

from apps.analyzer._cache import cached_or_compute
from apps.integrations.services.openpagerank import fetch_page_rank

# Open PageRank scores are 0-10; the tool shows a familiar 0-100 Domain Rating.
PAGERANK_TO_DR = 10
CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days — Open PageRank refreshes ~monthly.

# Hostname validation: 1+ labels, a TLD of 2+ letters, no scheme/path/spaces.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$"
)


class InvalidDomain(ValueError):
    """Raised when the supplied domain is empty or malformed — view returns 400."""


def _normalize(domain: str) -> str:
    """Strip scheme, www., and any path/query so we're left with a bare host."""
    d = (domain or "").strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix) :]
    if d.startswith("www."):
        d = d[4:]
    return d.rstrip("/").split("/")[0]


def _dr(page_rank_decimal: float | None) -> int:
    """Scale a 0-10 Open PageRank score to a 0-100 rating, clamped."""
    value = round((page_rank_decimal or 0) * PAGERANK_TO_DR)
    return max(0, min(100, value))


def get_or_generate(domain: str) -> dict:
    """
    Validate ``domain`` and return its Domain Rating payload (cached 7d).

    Raises ``InvalidDomain`` if the input isn't a well-formed hostname. Lets
    ``OpenPageRankNotConfigured`` / ``OpenPageRankError`` propagate so the view
    can map them to 503 / 502.
    """
    target = _normalize(domain)
    if not target or not _DOMAIN_RE.match(target):
        raise InvalidDomain("Enter a valid domain, e.g. signalor.ai")

    return cached_or_compute(
        f"domain-rating:{target}",
        CACHE_TTL_SECONDS,
        lambda: _build(target),
    )


def _build(target: str) -> dict:
    pr = fetch_page_rank(target)
    return {
        "domain": target,
        "domain_rating": _dr(pr["page_rank_decimal"]),
        "global_rank": pr["global_rank"],
        "found": pr["found"],
        "fetched_at": timezone.now().isoformat(),
    }
