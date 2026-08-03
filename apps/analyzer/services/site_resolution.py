"""Resolving which site a run refers to, and surviving a flaky database.

Moved out of views/_shared.py: neither the origin-normalisation nor the
integration-fallback chain is view code, and services/blog_automation.py needs
them too — importing views from a service would invert the layering.
"""

import logging
from urllib.parse import urlparse

from apps.integrations.models import Integration
from apps.organizations.models import Organization
from core.db import safe_first as _safe_first  # noqa: F401  (re-export: 18 call sites)

from ..models import (
    AnalysisRun,
)

logger = logging.getLogger("apps")



def _normalize_origin(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    if not parsed.netloc:
        return ""
    scheme = parsed.scheme if parsed.scheme in ("http", "https") else "https"
    return f"{scheme}://{parsed.netloc}".rstrip("/")

def _resolve_crawl_site(email: str, run_id: int | None, analyzed_url: str) -> tuple[str, str]:
    """
    Resolve canonical site URL and source for crawl checks.
    Priority: analyzed URL -> analyzer run URL -> WordPress integration -> Shopify integration.
    """
    # Prefer the exact analyzed URL origin first to avoid checking a different
    # integration domain (e.g., myshopify.com vs custom storefront domain).
    analyzed_origin = _normalize_origin(analyzed_url)
    if analyzed_origin:
        return analyzed_origin, "analyzed_url"

    if run_id:
        run = _safe_first(
            AnalysisRun.objects.filter(pk=run_id),
            context="crawl-site run lookup",
        )
        if run and run.url:
            origin = _normalize_origin(run.url)
            if origin:
                return origin, "analyzer_run"

    org = _safe_first(
        Organization.objects.filter(owner_email=email),
        context="crawl-site org lookup",
    )
    if org:
        wp = _safe_first(
            Integration.objects.filter(
                organization=org,
                provider=Integration.Provider.WORDPRESS,
                is_active=True,
            ),
            context="crawl-site wordpress lookup",
        )
        if wp:
            site_url = _normalize_origin(str(wp.metadata.get("site_url", "")))
            if site_url:
                return site_url, "wordpress"

        shopify = _safe_first(
            Integration.objects.filter(
                organization=org,
                provider=Integration.Provider.SHOPIFY,
                is_active=True,
            ),
            context="crawl-site shopify lookup",
        )
        if shopify:
            shop_domain = str(shopify.metadata.get("shop_domain", "")).strip()
            if shop_domain:
                return _normalize_origin(f"https://{shop_domain}"), "shopify"

    return "", "unknown"

