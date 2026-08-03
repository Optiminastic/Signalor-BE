"""
Brand Domain Authority for the dashboard "Domain Authority" card.

Prefers Ahrefs (real DR + backlinks + linking websites) when ``AHREFS_API_TOKEN``
is configured; otherwise falls back to the free Open PageRank Domain Rating (DR +
global rank, no backlinks). The card renders whatever fields are present, so the
Ahrefs upgrade is a drop-in — no frontend change needed.

SOLID notes:
  * SRP — compose one authority payload for a brand domain.
  * Open/Closed — add a new provider by extending ``_build`` / the ``ahrefs``
    module; callers and the card are untouched.

Cached per domain (7 days) — both upstreams change slowly and Ahrefs calls cost
API units.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.analyzer.services.domain_rating import (
    InvalidDomain,
    is_valid_domain,
    normalize_domain,
)
from apps.integrations.services import ahrefs
from apps.integrations.services.openpagerank import fetch_page_rank
from core.cache.keys import cached_or_compute

logger = logging.getLogger("apps")

CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days

# Open PageRank scores are 0-10; the card shows a familiar 0-100 Domain Rating.
_PAGERANK_TO_DR = 10


def get_for_domain(domain: str) -> dict:
    """
    Validate ``domain`` and return its authority payload (cached 7 days).

    Shape: ``{domain, domain_rating, global_rank, backlinks, linking_websites,
    source, fetched_at}``. Fields the active source can't provide are ``None``.
    Raises :class:`InvalidDomain` for a malformed domain.
    """
    target = normalize_domain(domain)
    if not is_valid_domain(target):
        raise InvalidDomain("A valid brand domain is required.")
    return cached_or_compute(
        f"domain-authority:{target}",
        CACHE_TTL_SECONDS,
        lambda: _build(target),
    )


def _empty(domain: str) -> dict:
    return {
        "domain": domain,
        "domain_rating": None,
        "global_rank": None,
        "backlinks": None,
        "linking_websites": None,
        "source": None,
        "fetched_at": timezone.now().isoformat(),
    }


def _from_ahrefs(domain: str) -> dict | None:
    """Ahrefs payload, or None to signal the caller should fall back."""
    try:
        payload = _empty(domain)
        payload.update(ahrefs.fetch_domain_authority(domain))
        payload["source"] = "ahrefs"
        return payload
    except ahrefs.AhrefsError as exc:
        logger.warning("Ahrefs authority failed for %s: %s; falling back to Open PageRank", domain, exc)
        return None


def _from_open_page_rank(domain: str) -> dict:
    payload = _empty(domain)
    pr = fetch_page_rank(domain)
    if pr["found"]:
        payload["domain_rating"] = max(0, min(100, round(pr["page_rank_decimal"] * _PAGERANK_TO_DR)))
    payload["global_rank"] = pr["global_rank"]
    payload["source"] = "openpagerank"
    return payload


def _build(target: str) -> dict:
    if ahrefs.is_configured():
        ahrefs_payload = _from_ahrefs(target)
        if ahrefs_payload is not None:
            return ahrefs_payload
    return _from_open_page_rank(target)
