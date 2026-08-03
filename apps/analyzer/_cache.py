"""Deprecated shim. Import from ``core.cache`` instead.

This module moved to ``core/cache.py`` in Phase 1 of docs/modularization-plan.md.
It holds no analyzer domain knowledge, and ``organizations`` and ``github_agent``
were importing it from here, which is one of the six app-level cycles.

Kept for one release so workers and any in-flight branch keep booting.
Delete once no import of ``apps.analyzer._cache`` remains.
"""

from core.cache.keys import (  # noqa: F401
    BRAND_CARD_TTL,
    brand_card_key,
    cached_or_compute,
    invalidate_brand_card,
    invalidate_email_aggregates,
    invalidate_run_aggregates,
)

__all__ = [
    "BRAND_CARD_TTL",
    "brand_card_key",
    "cached_or_compute",
    "invalidate_brand_card",
    "invalidate_email_aggregates",
    "invalidate_run_aggregates",
]
