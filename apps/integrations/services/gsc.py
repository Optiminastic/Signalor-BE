"""
Google Search Console data fetching service.

Uses the Search Console REST API (Search Analytics + URL Inspection) with the
OAuth credentials stored on the Integration. We call the REST endpoints directly
with a bearer token rather than pulling in google-api-python-client — the same
OAuth client/secret used for GA4 is reused, just with the webmasters scope.

Property identifiers (``site_url``) are either URL-prefix properties
(``https://example.com/``) or domain properties (``sc-domain:example.com``).
"""

import logging
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

import requests

from apps.integrations.models import Integration
from apps.integrations.views import GSC_SCOPES, _build_credentials, _refresh_if_needed

logger = logging.getLogger("apps")

_SITES_URL = "https://www.googleapis.com/webmasters/v3/sites"
_SEARCH_ANALYTICS_URL = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
_SITEMAPS_URL = "https://www.googleapis.com/webmasters/v3/sites/{site}/sitemaps"
_INSPECT_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"

_TIMEOUT = 30

# Cap how many sitemap URLs we inventory so a huge site can't blow up the request.
_SITEMAP_URL_CAP = 2000


def _coverage_key(url: str) -> str:
    """
    Normalize a URL for indexed-vs-submitted comparison: ignore scheme (http vs
    https), lowercase the host, drop the fragment and any trailing slash. Path
    case and query string are preserved (they can be significant).
    """
    try:
        s = urlsplit(url.strip())
        netloc = s.netloc.lower()
        path = s.path.rstrip("/")
        return urlunsplit(("", netloc, path, s.query, "")).strip()
    except Exception:  # noqa: BLE001 — never let a bad URL break bucketing
        return url.strip().rstrip("/").lower()


def _parse_sitemap(content: bytes) -> tuple[list[str], list[str]]:
    """Parse sitemap XML → (child_sitemap_urls, page_urls). Handles namespaces."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return [], []
    locs = [
        el.text.strip()
        for el in root.iter()
        if el.tag.lower().endswith("loc") and el.text and el.text.strip()
    ]
    if root.tag.lower().endswith("sitemapindex"):
        return locs, []
    return [], locs


def _collect_sitemap_urls(sitemap_paths: list[str], cap: int = _SITEMAP_URL_CAP) -> list[str]:
    """
    Download the submitted sitemaps (following one level of sitemap-index nesting)
    and return the inventory of page URLs they declare, de-duplicated and capped.
    Sitemaps are public documents, so no auth header is sent.
    """
    pages: list[str] = []
    seen_pages: set[str] = set()
    seen_sitemaps: set[str] = set()
    queue = list(sitemap_paths)
    while queue and len(pages) < cap:
        sm_url = queue.pop(0)
        if sm_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sm_url)
        try:
            resp = requests.get(
                sm_url,
                timeout=_TIMEOUT,
                headers={"User-Agent": "Signalor-GSC/1.0"},
            )
            if resp.status_code != 200:
                continue
            children, urls = _parse_sitemap(resp.content)
        except Exception as exc:  # noqa: BLE001 — one bad sitemap shouldn't fail all
            logger.warning("GSC coverage: sitemap fetch failed %s: %s", sm_url, exc)
            continue
        for child in children:
            if child not in seen_sitemaps:
                queue.append(child)
        for url in urls:
            key = _coverage_key(url)
            if key not in seen_pages:
                seen_pages.add(key)
                pages.append(url)
                if len(pages) >= cap:
                    break
    return pages


def _bearer(integration: Integration) -> str:
    """Return a fresh access token for this integration (refreshing if needed)."""
    creds = _build_credentials(integration, scopes=GSC_SCOPES)
    creds = _refresh_if_needed(integration, creds)
    return creds.token


def list_gsc_sites(integration: Integration) -> list[dict]:
    """
    Return the verified Search Console properties for the connected account.

    Each entry: {"site_url": "...", "permission_level": "siteOwner"}.
    Only properties the user can read are returned.
    """
    token = _bearer(integration)
    resp = requests.get(
        _SITES_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        logger.error("GSC list sites failed: %s %s", resp.status_code, resp.text)
        raise ValueError(f"Failed to list Search Console sites (HTTP {resp.status_code}).")

    entries = resp.json().get("siteEntry", []) or []
    sites = []
    for entry in entries:
        level = entry.get("permissionLevel", "")
        # siteUnverifiedUser can't query data — skip it.
        if level == "siteUnverifiedUser":
            continue
        sites.append(
            {
                "site_url": entry.get("siteUrl", ""),
                "permission_level": level,
            }
        )
    return sites


def _query(token: str, site_url: str, body: dict) -> list[dict]:
    """Run a Search Analytics query and return the ``rows`` list (empty on no data)."""
    url = _SEARCH_ANALYTICS_URL.format(site=quote(site_url, safe=""))
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        logger.error("GSC search analytics failed: %s %s", resp.status_code, resp.text)
        raise ValueError(f"Search Console query failed (HTTP {resp.status_code}).")
    return resp.json().get("rows", []) or []


def fetch_gsc_data(integration: Integration, days: int = 30) -> dict:
    """
    Fetch Search Console performance data for the selected property.

    Returns a dict with totals + daily_trend, top_queries, top_pages, countries.
    GSC data lags ~2-3 days, so the window ends 3 days before today.
    """
    site_url = integration.metadata.get("site_url")
    if not site_url:
        raise ValueError("No Search Console property selected for this integration.")

    token = _bearer(integration)

    # GSC finalizes data with a lag; end the range a few days back.
    end_date = date.today() - timedelta(days=3)
    start_date = end_date - timedelta(days=days)
    start_iso = start_date.isoformat()
    end_iso = end_date.isoformat()

    # 1. Totals (no dimensions = single aggregate row)
    totals = {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0}
    total_rows = _query(
        token,
        site_url,
        {"startDate": start_iso, "endDate": end_iso, "dimensions": []},
    )
    if total_rows:
        r = total_rows[0]
        totals = {
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": round(float(r.get("ctr", 0.0)), 4),
            "position": round(float(r.get("position", 0.0)), 1),
        }

    # 2. Daily trend
    daily_trend = [
        {
            "date": row["keys"][0],
            "clicks": int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr": round(float(row.get("ctr", 0.0)), 4),
            "position": round(float(row.get("position", 0.0)), 1),
        }
        for row in _query(
            token,
            site_url,
            {
                "startDate": start_iso,
                "endDate": end_iso,
                "dimensions": ["date"],
                "rowLimit": 10000,
            },
        )
    ]
    daily_trend.sort(key=lambda d: d["date"])

    # 3. Top queries
    top_queries = [
        {
            "query": row["keys"][0],
            "clicks": int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr": round(float(row.get("ctr", 0.0)), 4),
            "position": round(float(row.get("position", 0.0)), 1),
        }
        for row in _query(
            token,
            site_url,
            {
                "startDate": start_iso,
                "endDate": end_iso,
                "dimensions": ["query"],
                "rowLimit": 25,
            },
        )
    ]

    # 4. Top pages
    top_pages = [
        {
            "page": row["keys"][0],
            "clicks": int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr": round(float(row.get("ctr", 0.0)), 4),
            "position": round(float(row.get("position", 0.0)), 1),
        }
        for row in _query(
            token,
            site_url,
            {
                "startDate": start_iso,
                "endDate": end_iso,
                "dimensions": ["page"],
                "rowLimit": 25,
            },
        )
    ]

    # 5. Country breakdown
    countries = [
        {
            "country": row["keys"][0],  # ISO-3166-1 alpha-3, lowercase (e.g. "ind")
            "clicks": int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr": round(float(row.get("ctr", 0.0)), 4),
            "position": round(float(row.get("position", 0.0)), 1),
        }
        for row in _query(
            token,
            site_url,
            {
                "startDate": start_iso,
                "endDate": end_iso,
                "dimensions": ["country"],
                "rowLimit": 50,
            },
        )
    ]

    return {
        "date_start": start_iso,
        "date_end": end_iso,
        **totals,
        "daily_trend": daily_trend,
        "top_queries": top_queries,
        "top_pages": top_pages,
        "countries": countries,
    }


def fetch_gsc_page_metrics(integration: Integration, page_url: str, days: int = 30) -> dict:
    """
    Fetch Search Console metrics for a single analyzed page URL.

    Returns a best-effort page match payload with clicks/impressions/ctr/position.
    """
    empty = {
        "found": False,
        "page": page_url or "",
        "clicks": 0,
        "impressions": 0,
        "ctr": 0.0,
        "position": 0.0,
    }
    if not page_url:
        return empty

    site_url = integration.metadata.get("site_url")
    if not site_url:
        raise ValueError("No Search Console property selected for this integration.")

    token = _bearer(integration)
    end_date = date.today() - timedelta(days=3)
    start_date = end_date - timedelta(days=days)

    rows = _query(
        token,
        site_url,
        {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["page"],
            "dimensionFilterGroups": [
                {
                    "filters": [
                        {
                            "dimension": "page",
                            "operator": "equals",
                            "expression": page_url,
                        }
                    ]
                }
            ],
            "rowLimit": 1,
        },
    )
    if not rows:
        return empty

    r = rows[0]
    return {
        "found": True,
        "page": r["keys"][0],
        "clicks": int(r.get("clicks", 0)),
        "impressions": int(r.get("impressions", 0)),
        "ctr": round(float(r.get("ctr", 0.0)), 4),
        "position": round(float(r.get("position", 0.0)), 1),
    }


def inspect_gsc_url(integration: Integration, page_url: str, token: str | None = None) -> dict:
    """
    Run the URL Inspection API for a single URL against the selected property.

    Returns a normalized verdict: whether the URL is on Google, coverage state,
    last crawl time, robots/indexing verdicts, and the canonical URLs.

    Pass ``token`` to reuse a pre-fetched bearer across many inspections (avoids a
    DB hit + token rebuild per call) — important when inspecting in parallel.
    """
    site_url = integration.metadata.get("site_url")
    if not site_url:
        raise ValueError("No Search Console property selected for this integration.")
    if not page_url:
        raise ValueError("A URL to inspect is required.")

    token = token or _bearer(integration)
    resp = requests.post(
        _INSPECT_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"inspectionUrl": page_url, "siteUrl": site_url},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        logger.error("GSC URL inspection failed: %s %s", resp.status_code, resp.text)
        raise ValueError(f"URL inspection failed (HTTP {resp.status_code}).")

    result = resp.json().get("inspectionResult", {}) or {}
    index = result.get("indexStatusResult", {}) or {}
    verdict = index.get("verdict", "VERDICT_UNSPECIFIED")

    return {
        "inspected_url": page_url,
        "on_google": verdict == "PASS",
        "verdict": verdict,
        "coverage_state": index.get("coverageState", ""),
        "robots_txt_state": index.get("robotsTxtState", ""),
        "indexing_state": index.get("indexingState", ""),
        "last_crawl_time": index.get("lastCrawlTime", ""),
        "page_fetch_state": index.get("pageFetchState", ""),
        "google_canonical": index.get("googleCanonical", ""),
        "user_canonical": index.get("userCanonical", ""),
        "crawled_as": index.get("crawledAs", ""),
    }


def fetch_gsc_sitemaps(integration: Integration, token: str | None = None) -> dict:
    """
    List the sitemaps submitted for the selected property and their submit/index
    counts (``sitemaps.list``). Works for both URL-prefix and domain properties.

    Returns ``{"sitemaps": [...], "submitted": int, "indexed": int}``. Note: the
    per-sitemap ``indexed`` count is deprecated by Google and frequently returns 0
    even when pages are indexed — treat ``submitted`` as the reliable signal.
    """
    site_url = integration.metadata.get("site_url")
    if not site_url:
        raise ValueError("No Search Console property selected for this integration.")

    token = token or _bearer(integration)
    resp = requests.get(
        _SITEMAPS_URL.format(site=quote(site_url, safe="")),
        headers={"Authorization": f"Bearer {token}"},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        logger.error("GSC list sitemaps failed: %s %s", resp.status_code, resp.text)
        raise ValueError(f"Failed to list sitemaps (HTTP {resp.status_code}).")

    entries = resp.json().get("sitemap", []) or []
    sitemaps = []
    total_submitted = 0
    total_indexed = 0
    for entry in entries:
        # ``contents`` is a per-content-type breakdown (web, image, video, ...).
        contents = entry.get("contents", []) or []
        submitted = sum(int(c.get("submitted", 0) or 0) for c in contents)
        indexed = sum(int(c.get("indexed", 0) or 0) for c in contents)
        total_submitted += submitted
        total_indexed += indexed
        sitemaps.append(
            {
                "path": entry.get("path", ""),
                "type": entry.get("type", ""),
                "is_index": bool(entry.get("isSitemapsIndex", False)),
                "is_pending": bool(entry.get("isPending", False)),
                "last_submitted": entry.get("lastSubmitted", ""),
                "last_downloaded": entry.get("lastDownloaded", ""),
                "warnings": int(entry.get("warnings", 0) or 0),
                "errors": int(entry.get("errors", 0) or 0),
                "submitted": submitted,
                "indexed": indexed,
            }
        )

    return {
        "sitemaps": sitemaps,
        "submitted": total_submitted,
        "indexed": total_indexed,
    }


def fetch_served_pages(integration: Integration, days: int = 90) -> dict:
    """
    Pages Google served in Search (received impressions) over the window, with
    their clicks/impressions/ctr/position. Used to enrich the indexed-page list
    with real Search metrics. GSC data lags ~2-3 days.
    """
    site_url = integration.metadata.get("site_url")
    if not site_url:
        raise ValueError("No Search Console property selected for this integration.")

    token = _bearer(integration)
    end_date = date.today() - timedelta(days=3)
    start_date = end_date - timedelta(days=days)
    start_iso = start_date.isoformat()
    end_iso = end_date.isoformat()

    pages = [
        {
            "url": row["keys"][0],
            "clicks": int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr": round(float(row.get("ctr", 0.0)), 4),
            "position": round(float(row.get("position", 0.0)), 1),
        }
        for row in _query(
            token,
            site_url,
            {
                "startDate": start_iso,
                "endDate": end_iso,
                "dimensions": ["page"],
                "rowLimit": 1000,
            },
        )
    ]
    pages.sort(key=lambda p: p["impressions"], reverse=True)
    return {"date_start": start_iso, "date_end": end_iso, "pages": pages}


# Bound how many URLs we inspect per sync so a large site can't blow the daily
# URL-Inspection quota (2,000/day per property).
_INSPECT_CAP = 300
# Parallelism for the inspection pass — the API is ~8s/call but allows 600/min,
# so concurrency is the only way to keep the whole pass under a minute.
_INSPECT_CONCURRENCY = 8


def inspect_sitemap_index_status(integration: Integration, cap: int = _INSPECT_CAP) -> dict:
    """
    Authoritative index status for the property: inventory the submitted sitemap
    URLs, then run the URL Inspection API on each to get Google's real verdict
    (``on_google``) and coverage reason. This is the same data the Search Console
    "Page indexing" report shows — there is no bulk API for that report, so we
    derive it per-URL. Slow + rate-limited; intended to run in a background sync.

    Returns counts + a per-page list: each ``{url, on_google, coverage_state,
    verdict, last_crawl_time, robots_txt_state, indexing_state}``.
    """
    site_url = integration.metadata.get("site_url")
    if not site_url:
        raise ValueError("No Search Console property selected for this integration.")

    sm = fetch_gsc_sitemaps(integration)
    sitemap_urls = _collect_sitemap_urls([s["path"] for s in sm["sitemaps"]])[:cap]

    # The URL Inspection API is ~8s per call, so inspect in parallel. A shared,
    # pre-fetched token avoids a DB hit + token rebuild in each worker thread.
    token = _bearer(integration)

    def _inspect_one(url: str) -> dict:
        try:
            r = inspect_gsc_url(integration, url, token=token)
            return {
                "url": url,
                "on_google": bool(r["on_google"]),
                "coverage_state": r["coverage_state"],
                "verdict": r["verdict"],
                "last_crawl_time": r["last_crawl_time"],
                "robots_txt_state": r["robots_txt_state"],
                "indexing_state": r["indexing_state"],
            }
        except Exception as exc:  # noqa: BLE001 — one failed URL shouldn't kill the pass
            logger.warning("GSC index sync: inspection failed for %s: %s", url, exc)
            return {
                "url": url,
                "on_google": False,
                "coverage_state": "Could not verify",
                "verdict": "",
                "last_crawl_time": "",
                "robots_txt_state": "",
                "indexing_state": "",
            }

    results: list[dict] = []
    if sitemap_urls:
        with ThreadPoolExecutor(max_workers=_INSPECT_CONCURRENCY) as pool:
            results = list(pool.map(_inspect_one, sitemap_urls))

    indexed_count = sum(1 for r in results if r["on_google"])
    return {
        "checked_count": len(results),
        "indexed_count": indexed_count,
        "not_indexed_count": len(results) - indexed_count,
        "sitemap_total": len(sitemap_urls),
        "submitted": sm["submitted"],
        "pages": results,
    }


def normalize_site_host(site_url: str) -> str:
    """Best-effort hostname for a GSC property (handles sc-domain: and URL prefixes)."""
    if site_url.startswith("sc-domain:"):
        return site_url.removeprefix("sc-domain:").strip().lower()
    return (urlparse(site_url).hostname or "").lower()
