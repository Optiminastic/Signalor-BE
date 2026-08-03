"""Fetching page HTML: by integration, by snapshot, or direct."""

import logging

from ..models import (
    AnalysisRun,
)
from ..pipeline.crawler import CrawlResult

logger = logging.getLogger("apps")


def _crawl_result_from_html(url: str, html: str, *, min_len: int = 50) -> CrawlResult | None:
    """Build a scoreable CrawlResult from pre-fetched HTML (no live crawl).

    Returns None when the HTML is too short to analyze meaningfully. Shared by
    the integration and Next.js-snapshot fallbacks so they populate the exact
    same fields the live crawler would (html/soup/text/internal_links/is_https).
    """
    from bs4 import BeautifulSoup

    from ..pipeline.utils import extract_internal_links, extract_text

    if not html or len(html.strip()) < min_len:
        return None
    soup = BeautifulSoup(html, "html.parser")
    text = extract_text(soup)
    return CrawlResult(
        url=url,
        status_code=200,
        html=html,
        soup=soup,
        text=text,
        internal_links=extract_internal_links(soup, url),
        load_time=0.0,
        error="",
        is_https=url.startswith("https"),
    )


def _crawl_via_integration(run: AnalysisRun) -> CrawlResult | None:
    """Fallback: fetch page content via Shopify/WordPress API when public crawl fails."""
    from apps.integrations.models import Integration

    if not run.organization:
        return None

    integration = Integration.objects.filter(
        organization=run.organization,
        is_active=True,
        provider__in=["shopify", "wordpress"],
    ).first()

    if not integration:
        return None

    try:
        from ..auto_fix import _read_page_content

        html_content = _read_page_content(integration, run.url)

        result = _crawl_result_from_html(run.url, html_content or "")
        if result is None:
            logger.warning("Run %d: integration content missing/too short, skipping", run.id)
            return None

        logger.info(
            "Run %d: crawled via %s integration (API fallback, %d chars, %d text chars)",
            run.id,
            integration.provider,
            len(result.html),
            len(result.text),
        )
        return result
    except Exception as e:
        logger.warning("Run %d: integration crawl fallback failed: %s", run.id, e)
        return None


def _crawl_via_nextjs_snapshot(
    run: AnalysisRun,
) -> tuple[CrawlResult, list[CrawlResult]] | None:
    """Fetch homepage + key routes via the @signalor/nextjs snapshot route.

    Returns ``(homepage_crawl, additional_crawls)`` when the homepage renders,
    else None so the caller falls back to the live crawl. Bypasses Cloudflare /
    Turnstile because the SDK serves the site's own rendered HTML from its
    deployment origin (e.g. *.vercel.app), not the public CDN-fronted URL.
    """
    from ..services import nextjs_snapshot

    config = nextjs_snapshot.get_config(run)
    if config is None:
        return None

    origin, key_hash = config["origin"], config["key_hash"]
    base = run.url.rstrip("/")

    def _pull(path: str) -> CrawlResult | None:
        try:
            status, html = nextjs_snapshot.fetch_snapshot(origin, path, key_hash)
        except Exception as exc:  # noqa: BLE001 — transport/JSON error → treat as miss
            logger.warning("Run %d: snapshot pull failed for %s: %s", run.id, path, exc)
            return None
        if status != 200:
            return None
        # Map the route back onto the run's public URL so scorers and saved
        # PageScores reference the real site, not the deployment origin.
        page_url = f"{base}/" if path == "/" else f"{base}{path}"
        return _crawl_result_from_html(page_url, html)

    homepage = _pull("/")
    if homepage is None or not homepage.ok:
        return None

    additional: list[CrawlResult] = []
    for path in nextjs_snapshot.routes_for_run(run):
        if path == "/":
            continue
        extra = _pull(path)
        if extra is not None and extra.ok:
            additional.append(extra)

    logger.info(
        "Run %d: snapshot crawl ok — homepage + %d pages via @signalor/nextjs",
        run.id,
        len(additional),
    )
    return homepage, additional


def _robots_txt_for(crawl) -> str:
    """Fetch robots.txt for the crawled site, reusing the crawl's session."""
    try:
        from ..pipeline.crawler import fetch_file_content

        return fetch_file_content(crawl.url, "robots.txt", session=getattr(crawl, "session", None)) or ""
    except Exception:
        logger.warning("robots.txt fetch failed for %s", getattr(crawl, "url", "?"), exc_info=True)
        return ""

