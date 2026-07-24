"""Off-page presence verification — "is this brand already on <domain>?".

The brand-aware, works-for-everyone answer to bad off-page tasks ("get mentioned
on X"). Instead of a static platform blocklist (which is blind to who the brand
is), this runs a ``site:<domain> "<brand>"`` search and checks whether the brand
actually appears. It therefore adapts per brand:

- Vercel is already all over TechCrunch/Medium → those tasks are suppressed.
- A brand-new startup that isn't there → the task is kept (a real opportunity).

Degrades gracefully and safely: no Serper key, an API error, or a name-collision
result returns ``None`` (UNKNOWN) so the caller KEEPS the task — we never suppress
on uncertainty. Cached per (brand, domain) for 7 days so it's cheap and rate-safe.
"""

from __future__ import annotations

import logging

from apps.analyzer.pipeline import serper
from apps.analyzer.pipeline.utils import compute_entity_confidence

logger = logging.getLogger("apps")

_CACHE_TTL = 60 * 60 * 24 * 7  # 7 days
_MIN_CONFIDENCE = 0.6  # a result must plausibly be THIS brand, not a same-named entity


def _cache_key(brand: str, domain: str) -> str:
    return f"offpage_presence:{brand.strip().lower()}:{domain.strip().lower()}"


def _query_presence(brand: str, domain: str, industry: str) -> bool | None:
    """Search the domain for the brand. True = present, False = confidently absent,
    None = couldn't determine (keep the task)."""
    try:
        data = serper.search(f'site:{domain} "{brand}"', num=10)
    except Exception:
        logger.warning("offpage presence search failed (%s on %s)", brand, domain, exc_info=True)
        return None
    if not data:
        return None
    organic = data.get("organic") or []
    if not organic:
        return False  # the domain has no page matching the brand → a real gap
    # Guard against same-name collisions: at least one result must plausibly be this brand.
    for item in organic[:10]:
        text = " ".join(str(item.get(k, "")) for k in ("title", "snippet", "link"))
        if compute_entity_confidence(brand, text, domain=domain, industry=industry) >= _MIN_CONFIDENCE:
            return True
    return False


def brand_present_on_domain(brand_name: str, domain: str, *, industry: str = "") -> bool | None:
    """True if the brand already appears on ``domain``; False if confidently absent;
    None if undeterminable (no search configured / error) → caller keeps the task."""
    from django.core.cache import cache

    brand = (brand_name or "").strip()
    dom = (domain or "").strip().lower().lstrip(".")
    if not brand or not dom or not serper.is_configured():
        return None

    key = _cache_key(brand, dom)
    try:
        cached = cache.get(key)
    except Exception:
        cached = None
    if cached is not None:
        return cached

    result = _query_presence(brand, dom, industry)
    if result is not None:
        try:
            cache.set(key, result, _CACHE_TTL)
        except Exception:
            logger.warning("offpage presence cache.set failed", exc_info=True)
    return result
