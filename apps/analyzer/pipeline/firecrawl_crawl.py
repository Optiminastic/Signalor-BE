"""Thin client for the Firecrawl hosted crawl API (v2).

Firecrawl recursively discovers and scrapes a site. Unlike a single-shot API,
a crawl is a *job*: we POST to start it, then poll until it completes and
collect every page (following ``next`` pagination for large result sets).

We request the ``rawHtml`` format with ``onlyMainContent: False`` so the full
page HTML — including ``<script type="application/ld+json">`` — reaches the
analyzer's BeautifulSoup / schema parsing unchanged.

The API key is read from ``FIRECRAWL_API_KEY`` (managed outside the codebase).
When unset, ``is_configured()`` is False and callers fall back to the direct
crawler.
"""

import logging
import os
import time

import requests

logger = logging.getLogger("apps")

CRAWL_ENDPOINT = "https://api.firecrawl.dev/v2/crawl"
HTTP_TIMEOUT = 30  # per request (start / poll)
POLL_INTERVAL = 3  # seconds between status polls
# Total wall-clock budget for a crawl job to finish; on timeout we return
# whatever pages are ready so the pipeline can still score the homepage.
MAX_WAIT = 120


class FirecrawlError(Exception):
    """Raised when a Firecrawl crawl cannot be completed."""


def _api_key() -> str:
    return os.getenv("FIRECRAWL_API_KEY", "").strip()


def is_configured() -> bool:
    """True when a Firecrawl API key is present in the environment."""
    return bool(_api_key())


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }


def crawl(url: str, limit: int = 15, max_wait: int = MAX_WAIT) -> list[dict]:
    """Crawl ``url`` (and discovered pages) via Firecrawl, waiting for the job.

    Returns Firecrawl's list of page documents, each shaped like
    ``{"rawHtml"/"html": str, "markdown": str, "metadata": {"sourceURL", "statusCode", ...}}``.

    Raises :class:`FirecrawlError` on any failure (not configured, transport
    error, payment required, non-2xx, failed job) so callers can fall back.
    """
    if not is_configured():
        raise FirecrawlError("FIRECRAWL_API_KEY not configured")

    payload = {
        "url": url,
        "limit": max(1, int(limit)),
        "scrapeOptions": {
            "formats": ["rawHtml"],
            "onlyMainContent": False,
        },
    }

    # ── Start the crawl job ──
    try:
        resp = requests.post(CRAWL_ENDPOINT, headers=_headers(), json=payload, timeout=HTTP_TIMEOUT)
    except requests.RequestException as exc:
        raise FirecrawlError(f"start request failed: {exc}") from exc

    if resp.status_code == 402:
        raise FirecrawlError("payment required (402) — out of Firecrawl credits or limit exceeds balance")
    if resp.status_code not in (200, 201):
        raise FirecrawlError(f"start HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        job = resp.json()
    except ValueError as exc:
        raise FirecrawlError(f"start returned non-JSON: {exc}") from exc

    status_url = job.get("url") or (f"{CRAWL_ENDPOINT}/{job.get('id')}" if job.get("id") else None)
    if not status_url:
        raise FirecrawlError(f"no job id/url in start response: {str(job)[:200]}")

    # ── Poll until completed (or failed / timed out) ──
    pages: list[dict] = []
    started = time.time()
    while True:
        try:
            poll = requests.get(status_url, headers=_headers(), timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            raise FirecrawlError(f"status poll failed: {exc}") from exc
        if poll.status_code != 200:
            raise FirecrawlError(f"status HTTP {poll.status_code}: {poll.text[:300]}")

        try:
            data = poll.json()
        except ValueError as exc:
            raise FirecrawlError(f"status returned non-JSON: {exc}") from exc

        status = data.get("status")
        if status == "completed":
            pages.extend(data.get("data") or [])
            nxt = data.get("next")
            seen: set[str] = set()
            while nxt and nxt not in seen:
                seen.add(nxt)
                try:
                    page_resp = requests.get(nxt, headers=_headers(), timeout=HTTP_TIMEOUT)
                except requests.RequestException:
                    break
                if page_resp.status_code != 200:
                    break
                page_json = page_resp.json()
                pages.extend(page_json.get("data") or [])
                nxt = page_json.get("next")
            logger.info(
                "Firecrawl crawl completed for %s: %d pages (creditsUsed=%s, total=%s)",
                url,
                len(pages),
                data.get("creditsUsed"),
                data.get("total"),
            )
            break

        if status == "failed":
            raise FirecrawlError(f"crawl job failed: {str(data)[:200]}")

        if time.time() - started > max_wait:
            # Return whatever the latest snapshot has so the homepage can score.
            pages.extend(data.get("data") or [])
            logger.warning(
                "Firecrawl crawl timed out after %ss for %s (status=%s, %d pages so far)",
                max_wait,
                url,
                status,
                len(pages),
            )
            break

        time.sleep(POLL_INTERVAL)

    return pages
