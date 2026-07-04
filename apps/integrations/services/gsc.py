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
from datetime import date, timedelta
from urllib.parse import quote, urlparse

import requests

from apps.integrations.models import Integration
from apps.integrations.views import GSC_SCOPES, _build_credentials, _refresh_if_needed

logger = logging.getLogger("apps")

_SITES_URL = "https://www.googleapis.com/webmasters/v3/sites"
_SEARCH_ANALYTICS_URL = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
_INSPECT_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"

_TIMEOUT = 30


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


def inspect_gsc_url(integration: Integration, page_url: str) -> dict:
    """
    Run the URL Inspection API for a single URL against the selected property.

    Returns a normalized verdict: whether the URL is on Google, coverage state,
    last crawl time, robots/indexing verdicts, and the canonical URLs.
    """
    site_url = integration.metadata.get("site_url")
    if not site_url:
        raise ValueError("No Search Console property selected for this integration.")
    if not page_url:
        raise ValueError("A URL to inspect is required.")

    token = _bearer(integration)
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


_SITEMAPS_URL = "https://www.googleapis.com/webmasters/v3/sites/{site}/sitemaps"


def list_gsc_sitemaps(integration: Integration) -> dict:
    """
    List the sitemaps Google knows about for the selected property, with the
    submitted/indexed counts, errors, and warnings straight from Search Console.

    Returns {"sitemaps": [...], "submitted": int, "indexed": int}.
    """
    site_url = integration.metadata.get("site_url")
    if not site_url:
        raise ValueError("No Search Console property selected for this integration.")

    token = _bearer(integration)
    url = _SITEMAPS_URL.format(site=quote(site_url, safe=""))
    resp = requests.get(
        url,
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
        submitted = 0
        indexed = 0
        for content in entry.get("contents", []) or []:
            submitted += int(content.get("submitted", 0) or 0)
            indexed += int(content.get("indexed", 0) or 0)
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


def fetch_gsc_coverage(integration: Integration, days: int = 90) -> dict:
    """
    Build a live index-coverage view from Search Console.

    Google exposes no full "coverage report" API, so we combine two real signals:
    - sitemap submitted/indexed totals (from the Sitemaps API), and
    - the pages Google actually served in Search over the window (Search Analytics
      ``page`` dimension) — every such page is crawled and indexed.

    Returns {"submitted", "indexed", "served_count", "pages": [...], date range}.
    """
    site_url = integration.metadata.get("site_url")
    if not site_url:
        raise ValueError("No Search Console property selected for this integration.")

    token = _bearer(integration)
    end_date = date.today() - timedelta(days=3)
    start_date = end_date - timedelta(days=days)

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
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "dimensions": ["page"],
                "rowLimit": 1000,
            },
        )
    ]
    pages.sort(key=lambda p: p["impressions"], reverse=True)

    # Sitemap submitted/indexed totals are a best-effort enrichment — never fail
    # coverage just because the property has no submitted sitemap.
    submitted = 0
    indexed = 0
    try:
        sm = list_gsc_sitemaps(integration)
        submitted = sm["submitted"]
        indexed = sm["indexed"]
    except ValueError:
        pass

    return {
        "date_start": start_date.isoformat(),
        "date_end": end_date.isoformat(),
        "submitted": submitted,
        "indexed": indexed,
        "served_count": len(pages),
        "pages": pages,
    }


def normalize_site_host(site_url: str) -> str:
    """Best-effort hostname for a GSC property (handles sc-domain: and URL prefixes)."""
    if site_url.startswith("sc-domain:"):
        return site_url.removeprefix("sc-domain:").strip().lower()
    return (urlparse(site_url).hostname or "").lower()
