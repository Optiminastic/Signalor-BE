"""
DataForSEO Backlinks API client.

Provides batch enrichment of domain authority and backlink metrics for the
Citation Authority panel.

Auth: HTTP Basic with DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD env vars.
Endpoints used (live mode = no queue, immediate response):
    POST /v3/backlinks/bulk_ranks/live              -> rank (0-1000)
    POST /v3/backlinks/bulk_referring_domains/live  -> referring_domains, backlinks
"""
from __future__ import annotations

import logging
from typing import Iterable

import requests
from django.conf import settings

logger = logging.getLogger("apps")

API_BASE = "https://api.dataforseo.com/v3"
TIMEOUT_SECONDS = 30
DATAFORSEO_OK_STATUS = 20000


class DataForSEONotConfigured(RuntimeError):
    """Raised when DataForSEO credentials are missing from settings."""


class DataForSEOError(RuntimeError):
    """Raised when DataForSEO returns a non-success status code."""


def _auth() -> tuple[str, str]:
    login = getattr(settings, "DATAFORSEO_LOGIN", "") or ""
    password = getattr(settings, "DATAFORSEO_PASSWORD", "") or ""
    if not login or not password:
        raise DataForSEONotConfigured(
            "DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD env vars are not set."
        )
    return (login, password)


def _post(path: str, payload: list[dict]) -> dict:
    from apps.integrations._http import request_with_retry

    resp = request_with_retry(
        "POST",
        f"{API_BASE}{path}",
        json=payload,
        auth=_auth(),
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("status_code") != DATAFORSEO_OK_STATUS:
        raise DataForSEOError(
            f"{path}: {body.get('status_code')} {body.get('status_message')}"
        )
    # DataForSEO returns 200/20000 at the envelope level even when individual
    # tasks fail (auth scope, missing subscription, malformed target). Surface
    # the first per-task failure so callers see the real problem.
    for task in body.get("tasks") or []:
        task_status = task.get("status_code")
        if task_status and task_status != DATAFORSEO_OK_STATUS:
            raise DataForSEOError(
                f"{path}: {task_status} {task.get('status_message')}"
            )
    return body


def _extract_items(body: dict) -> list[dict]:
    """Flatten DataForSEO's tasks -> result -> items envelope into a single list."""
    items: list[dict] = []
    for task in body.get("tasks") or []:
        for result in task.get("result") or []:
            for item in result.get("items") or []:
                items.append(item)
    return items


def fetch_domain_metrics(domains: Iterable[str]) -> dict[str, dict]:
    """
    Batch-fetch backlink metrics for a set of bare domains (no scheme/path).

    Returns {domain: {"referring_domains": int, "backlinks": int, "rank": int}}.
    Domains with no data appear with zero values.
    """
    targets = sorted({d.strip().lower() for d in domains if d and d.strip()})
    if not targets:
        return {}

    rd_body = _post(
        "/backlinks/bulk_referring_domains/live",
        [{"targets": targets}],
    )
    rank_body = _post(
        "/backlinks/bulk_ranks/live",
        [{"targets": targets}],
    )

    rd_by_target = {
        (row.get("target") or "").lower(): row
        for row in _extract_items(rd_body)
        if row.get("target")
    }
    rank_by_target = {
        (row.get("target") or "").lower(): row
        for row in _extract_items(rank_body)
        if row.get("target")
    }

    out: dict[str, dict] = {}
    for d in targets:
        rd_row = rd_by_target.get(d, {})
        rank_row = rank_by_target.get(d, {})
        out[d] = {
            "referring_domains": int(rd_row.get("referring_domains") or 0),
            "backlinks": int(rd_row.get("backlinks") or 0),
            "rank": int(rank_row.get("rank") or 0),
        }
    return out


def _normalize_target(domain: str) -> str:
    """Strip scheme, www., trailing slash. DataForSEO Labs expects bare domain."""
    d = (domain or "").strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    if d.startswith("www."):
        d = d[4:]
    return d.rstrip("/").split("/")[0]


def _first_result(body: dict) -> dict:
    for task in body.get("tasks") or []:
        for result in task.get("result") or []:
            return result
    return {}


def fetch_domain_overview(domain: str, location_code: int = 2840) -> dict:
    """
    DataForSEO Labs — Domain Rank Overview.

    Returns headline organic + paid metrics for a domain (estimated traffic,
    keyword count, traffic value in USD). location_code 2840 = United States;
    a domain with global presence still gets the largest dataset from US.
    """
    target = _normalize_target(domain)
    if not target:
        return {}
    body = _post(
        "/dataforseo_labs/google/domain_rank_overview/live",
        [{"target": target, "location_code": location_code, "language_code": "en"}],
    )
    result = _first_result(body)
    items = result.get("items") or []
    if not items:
        return {}
    metrics = items[0].get("metrics") or {}
    organic = metrics.get("organic") or {}
    paid = metrics.get("paid") or {}
    return {
        "organic_keywords": int(organic.get("count") or 0),
        "organic_traffic": int(organic.get("etv") or 0),
        "organic_value_usd": float(organic.get("estimated_paid_traffic_cost") or 0.0),
        "paid_keywords": int(paid.get("count") or 0),
        "paid_traffic": int(paid.get("etv") or 0),
        "paid_value_usd": float(paid.get("estimated_paid_traffic_cost") or 0.0),
    }


def fetch_ranked_keywords(
    domain: str, *, limit: int = 50, location_code: int = 2840
) -> list[dict]:
    """
    DataForSEO Labs — Ranked Keywords.

    Returns up to ``limit`` keywords the domain ranks for, ordered by
    estimated traffic value (etv) descending. Each item:
    {keyword, position, search_volume, etv, url}.
    """
    target = _normalize_target(domain)
    if not target:
        return []
    body = _post(
        "/dataforseo_labs/google/ranked_keywords/live",
        [
            {
                "target": target,
                "location_code": location_code,
                "language_code": "en",
                "limit": min(max(limit, 1), 100),
                "order_by": ["ranked_serp_element.serp_item.etv,desc"],
            }
        ],
    )
    out: list[dict] = []
    for item in (_first_result(body).get("items") or []):
        kw_data = item.get("keyword_data") or {}
        kw_info = kw_data.get("keyword_info") or {}
        ranked = (item.get("ranked_serp_element") or {}).get("serp_item") or {}
        out.append(
            {
                "keyword": kw_data.get("keyword") or "",
                "position": int(ranked.get("rank_absolute") or ranked.get("rank_group") or 0),
                "search_volume": int(kw_info.get("search_volume") or 0),
                "etv": float(ranked.get("etv") or 0.0),
                "url": ranked.get("url") or "",
            }
        )
    return out


# DataForSEO location_code -> ISO alpha-2. Curated set spans all 8 regions
# (NA, SA, EU, ME, AF, AS, SEA, OC) so the World Presence map covers the
# globe without paying for 195 country lookups.
GEO_COUNTRIES: list[tuple[int, str, str]] = [
    (2840, "US", "United States"),
    (2124, "CA", "Canada"),
    (2484, "MX", "Mexico"),
    (2076, "BR", "Brazil"),
    (2032, "AR", "Argentina"),
    (2826, "GB", "United Kingdom"),
    (2276, "DE", "Germany"),
    (2250, "FR", "France"),
    (2724, "ES", "Spain"),
    (2380, "IT", "Italy"),
    (2528, "NL", "Netherlands"),
    (2356, "IN", "India"),
    (2392, "JP", "Japan"),
    (2702, "SG", "Singapore"),
    (2360, "ID", "Indonesia"),
    (2458, "MY", "Malaysia"),
    (2784, "AE", "United Arab Emirates"),
    (2682, "SA", "Saudi Arabia"),
    (2792, "TR", "Turkey"),
    (2710, "ZA", "South Africa"),
    (2566, "NG", "Nigeria"),
    (2818, "EG", "Egypt"),
    (2036, "AU", "Australia"),
    (2554, "NZ", "New Zealand"),
]


def _fetch_country_overview(target: str, location_code: int, alpha2: str) -> tuple[str, dict | None]:
    """Single-country task. Returns (alpha2, metrics) or (alpha2, None) on miss/error."""
    try:
        body = _post(
            "/dataforseo_labs/google/domain_rank_overview/live",
            [{"target": target, "location_code": location_code, "language_code": "en"}],
        )
    except (DataForSEOError, requests.RequestException) as exc:
        logger.debug("geo overview failed for %s/%s: %s", target, alpha2, exc)
        return alpha2, None

    for task in body.get("tasks") or []:
        for result in task.get("result") or []:
            items = result.get("items") or []
            if not items:
                continue
            metrics = (items[0].get("metrics") or {}).get("organic") or {}
            traffic = int(metrics.get("etv") or 0)
            keywords = int(metrics.get("count") or 0)
            value = float(metrics.get("estimated_paid_traffic_cost") or 0.0)
            if traffic == 0 and keywords == 0:
                return alpha2, None
            return alpha2, {
                "organic_traffic": traffic,
                "organic_keywords": keywords,
                "organic_value_usd": value,
            }
    return alpha2, None


def fetch_domain_geo_distribution(domain: str) -> dict[str, dict]:
    """
    Per-country estimated organic traffic for a domain across the curated
    GEO_COUNTRIES list. The Labs API accepts only one task per POST, so we
    parallelize 8 at a time — ~3s total for the 24-country sweep.

    Returns {alpha2: {organic_traffic, organic_keywords, organic_value_usd}}.
    Countries with no data are omitted (so the map paints only true coverage,
    not fake every region).
    """
    from concurrent.futures import ThreadPoolExecutor

    target = _normalize_target(domain)
    if not target:
        return {}

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(_fetch_country_overview, target, loc, alpha2)
            for loc, alpha2, _name in GEO_COUNTRIES
        ]
        for fut in futures:
            alpha2, metrics = fut.result()
            if metrics:
                out[alpha2] = metrics
    return out


def fetch_relevant_pages(
    domain: str, *, limit: int = 20, location_code: int = 2840
) -> list[dict]:
    """
    DataForSEO Labs — Relevant Pages (top organic pages for a domain).

    Returns up to ``limit`` pages ordered by organic traffic. Each item:
    {url, organic_traffic, organic_keywords, value_usd}.
    """
    target = _normalize_target(domain)
    if not target:
        return []
    body = _post(
        "/dataforseo_labs/google/relevant_pages/live",
        [
            {
                "target": target,
                "location_code": location_code,
                "language_code": "en",
                "limit": min(max(limit, 1), 100),
                "order_by": ["metrics.organic.etv,desc"],
            }
        ],
    )
    out: list[dict] = []
    for item in (_first_result(body).get("items") or []):
        metrics = (item.get("metrics") or {}).get("organic") or {}
        out.append(
            {
                "url": item.get("page_address") or "",
                "organic_traffic": int(metrics.get("etv") or 0),
                "organic_keywords": int(metrics.get("count") or 0),
                "value_usd": float(metrics.get("estimated_paid_traffic_cost") or 0.0),
            }
        )
    return out
