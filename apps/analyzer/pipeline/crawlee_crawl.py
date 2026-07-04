"""Crawlee-based site crawler (HTTP + BeautifulSoup) for the analyzer.

Runs *in-process* (no API key, no credits) using Crawlee's BeautifulSoupCrawler:
it fetches the start URL and follows same-site links up to a page cap, returning
a list of ``{"url", "html", "status"}`` dicts that ``crawler.py`` adapts into
``CrawlResult``. HTTP-only — it does not render JavaScript, so SPA/JS-only
content won't be seen (that's the engine trade-off chosen for this integration).

Stateless: a fresh in-memory storage client per call so repeated analyses never
share a request queue / dedup set. Because it crawls from our own server IP,
sites behind Cloudflare / anti-bot may block it — callers fall back to the
direct crawler (+ scraper API) on failure.
"""

import asyncio
import logging
import os

logger = logging.getLogger("apps")

DEFAULT_LIMIT = 13
# Hard wall-clock guard so a slow/looping crawl can't hang the analysis thread.
DEFAULT_TIMEOUT = 90


class CrawleeError(Exception):
    """Raised when the Crawlee crawl cannot run or yields nothing."""


def is_configured() -> bool:
    """Crawlee needs no key, so it's available unless explicitly disabled via
    ``SIGNALOR_USE_CRAWLEE`` (defaults on)."""
    return os.getenv("SIGNALOR_USE_CRAWLEE", "true").strip().lower() not in ("0", "false", "no", "off")


def crawl(url: str, limit: int = DEFAULT_LIMIT, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    """Crawl ``url`` and return a list of ``{"url", "html", "status"}`` page dicts.

    Raises :class:`CrawleeError` on failure so callers can fall back. Safe to call
    from sync code (Celery worker / daemon thread); if an event loop is already
    running it executes in a dedicated thread.
    """
    try:
        try:
            asyncio.get_running_loop()
            loop_running = True
        except RuntimeError:
            loop_running = False

        if loop_running:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(_crawl_async(url, limit, timeout))).result()
        return asyncio.run(_crawl_async(url, limit, timeout))
    except CrawleeError:
        raise
    except Exception as exc:
        raise CrawleeError(f"Crawlee crawl failed: {exc}") from exc


async def _crawl_async(url: str, limit: int, timeout: int) -> list[dict]:
    from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext
    from crawlee.storage_clients import MemoryStorageClient

    pages: list[dict] = []

    crawler = BeautifulSoupCrawler(
        max_requests_per_crawl=max(1, int(limit)),
        storage_client=MemoryStorageClient(),
    )

    @crawler.router.default_handler
    async def _handler(context: BeautifulSoupCrawlingContext) -> None:
        # parsed_content is Crawlee's BeautifulSoup; serialising it back to HTML
        # preserves <script type="application/ld+json"> for the schema scorer.
        soup = context.parsed_content
        html = str(soup) if soup is not None else ""
        pages.append(
            {
                "url": context.request.url,
                "html": html,
                "status": getattr(context.http_response, "status_code", 0) or 0,
            }
        )
        # Follow same-site links up to the request cap.
        try:
            await context.enqueue_links()
        except Exception as exc:  # noqa: BLE001 - link discovery is best-effort
            logger.debug("crawlee enqueue_links failed for %s: %s", context.request.url, exc)

    try:
        await asyncio.wait_for(crawler.run([url]), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "Crawlee crawl timed out after %ss for %s (%d pages so far)", timeout, url, len(pages)
        )

    logger.info("Crawlee crawl finished for %s: %d pages", url, len(pages))
    return pages
