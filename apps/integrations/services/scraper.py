"""Scraping-API fallback for the crawler.

When a direct ``requests`` crawl is hard-blocked (typically HTTP 403 from a
Cloudflare/WAF in front of a site, triggered by our datacenter egress IPs), we
re-fetch the same URL through a third-party scraping API that egresses from
residential IPs. The fallback is OFF until ``SCRAPER_API_KEY`` is set, so default
crawl behavior is unchanged.

Mirrors the credential/exception pattern in
``apps/integrations/services/dataforseo.py`` and reuses
``apps/integrations/_http.request_with_retry`` for transport retries.

Providers (``SCRAPER_API_PROVIDER``):
  - ``scrapingbee`` (default): https://app.scrapingbee.com/api/v1/
  - ``scraperapi``:            https://api.scraperapi.com/
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from django.conf import settings

from apps.integrations._http import request_with_retry

logger = logging.getLogger("apps")

# Scraping APIs route through residential proxies (and optionally render JS), so
# they are slow — give them a generous timeout.
TIMEOUT_SECONDS = 70


class ScraperNotConfigured(RuntimeError):
    """Raised when SCRAPER_API_KEY is not set — fallback is disabled."""


class ScraperError(RuntimeError):
    """Raised when the scraping API call fails, or returns non-200 / empty body."""


def is_configured() -> bool:
    """True when a scraping-API key is configured (i.e. the fallback is active)."""
    return bool(getattr(settings, "SCRAPER_API_KEY", "") or "")


def _build_request(url: str, render_js: bool) -> tuple[str, dict]:
    """Return (endpoint, query_params) for the configured provider."""
    api_key = getattr(settings, "SCRAPER_API_KEY", "") or ""
    if not api_key:
        raise ScraperNotConfigured("SCRAPER_API_KEY is not set.")

    provider = (getattr(settings, "SCRAPER_API_PROVIDER", "") or "scrapingbee").strip().lower()
    if provider == "scraperapi":
        return (
            "https://api.scraperapi.com/",
            {"api_key": api_key, "url": url, "render": "true" if render_js else "false"},
        )
    if provider == "scrapingbee":
        return (
            "https://app.scrapingbee.com/api/v1/",
            {"api_key": api_key, "url": url, "render_js": "true" if render_js else "false"},
        )
    raise ScraperError(f"Unsupported SCRAPER_API_PROVIDER: {provider!r}")


def fetch_via_scraper(
    url: str,
    *,
    render_js: bool | None = None,
    timeout: float = TIMEOUT_SECONDS,
) -> tuple[int, str]:
    """Re-fetch ``url`` through the configured scraping API.

    Returns ``(status_code, html)`` where a 200 means the target page was served.
    Raises :class:`ScraperNotConfigured` when no key is set, or
    :class:`ScraperError` on transport failure / non-200 / empty body.
    """
    if render_js is None:
        render_js = bool(getattr(settings, "SCRAPER_RENDER_JS", False))

    endpoint, params = _build_request(url, render_js)
    provider = getattr(settings, "SCRAPER_API_PROVIDER", "scrapingbee")
    # Never log the assembled URL — it embeds the API key. Log only the target.
    logger.info("scraper fallback: fetching %s via %s (render_js=%s)", url, provider, render_js)

    full_url = f"{endpoint}?{urlencode(params)}"
    try:
        resp = request_with_retry("GET", full_url, timeout=timeout, max_retries=2)
    except Exception as exc:  # noqa: BLE001 — requests.RequestException & friends
        raise ScraperError(f"scraper transport error for {url}: {exc}") from exc

    html = resp.text or ""
    if resp.status_code != 200 or not html.strip():
        raise ScraperError(f"scraper returned {resp.status_code} / empty body for {url}")
    return 200, html
