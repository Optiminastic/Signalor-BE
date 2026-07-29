"""Google Search Visibility check (score 0-100).

Every value reported here is **observed** in real search results. There is
deliberately no LLM fallback: this check previously asked a model whether a brand
"likely" had a Knowledge Panel and how many pages Google had indexed, then scored
the answer. No model can know either, so the numbers were invented and presented
as measurements. When no search backend can answer, the check now returns 0 with
``unknown=True`` and the caller awards no points — the same contract as
``apps.analyzer.pipeline.serper``.

Strategies, in order:
  1. Google Custom Search API (if GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX configured)
  2. Serper (Google Search API) — the maintained path, shares SERPER_API_KEY
  3. googlesearch-python scraper (legacy, usually blocked)
"""

import logging
import os
from urllib.parse import urlparse

import requests

logger = logging.getLogger("apps")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Organic results to pull for the `site:<domain>` query. Serper returns no result
# total, so the indexed-page figure is a floor built from what we can actually see.
_SITE_INDEX_SAMPLE = 100


def check_google(brand_name: str, brand_url: str) -> tuple[float, dict]:
    """
    Check Google Search visibility for a brand.
    Returns (score, details_dict).

    Sub-scores:
      - brand_search_rank (40%): position of brand URL in brand-name search
      - site_index (30%): estimated indexed pages
      - brand_dominance (30%): how many of top results are the brand
    """
    domain = urlparse(brand_url).netloc.replace("www.", "")

    # Strategy 1: Google Custom Search API
    api_key = os.environ.get("GOOGLE_CSE_API_KEY", "")
    cse_cx = os.environ.get("GOOGLE_CSE_CX", "")
    if api_key and cse_cx:
        result = _check_via_cse_api(brand_name, domain, api_key, cse_cx)
        if result is not None:
            return result

    # Strategy 2: Serper
    result = _check_via_serper(brand_name, domain)
    if result is not None:
        return result

    # Strategy 3: googlesearch-python (usually blocked by Google)
    result = _check_via_scraper(brand_name, domain)
    if result is not None:
        return result

    # Nothing could observe Google. Unknown is not zero visibility, but it must
    # not be scored as visibility either — award nothing and say why.
    logger.warning("Google visibility for %s: no search backend available (result: unknown)", brand_name)
    return 0.0, {
        "method": "unavailable",
        "unknown": True,
        "brand_search_results": [],
        "site_index_estimate": 0,
        "brand_rank_position": None,
        "brand_results_count": 0,
        "total_results_checked": 0,
        "sub_scores": {},
        "error": "No search backend configured (set SERPER_API_KEY or GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX).",
    }


def _check_via_cse_api(
    brand_name: str, domain: str, api_key: str, cx: str
) -> tuple[float, dict] | None:
    """Use Google Custom Search JSON API (free: 100 queries/day)."""
    details = {
        "method": "google_cse_api",
        "brand_search_results": [],
        "site_index_estimate": 0,
        "brand_rank_position": None,
        "brand_results_count": 0,
        "total_results_checked": 0,
    }

    try:
        # Brand name search
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": api_key, "cx": cx, "q": brand_name, "num": 10},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("Google CSE API returned %d", resp.status_code)
            return None

        data = resp.json()
        items = data.get("items", [])
        details["total_results_checked"] = len(items)

        brand_rank = None
        brand_count = 0
        result_list = []

        for i, item in enumerate(items):
            url = item.get("link", "")
            result_domain = urlparse(url).netloc.replace("www.", "")
            is_brand = domain in result_domain or result_domain in domain
            result_list.append({
                "position": i + 1,
                "url": url[:200],
                "title": item.get("title", "")[:100],
                "snippet": item.get("snippet", "")[:200],
                "is_brand": is_brand,
            })
            if is_brand:
                brand_count += 1
                if brand_rank is None:
                    brand_rank = i + 1

        details["brand_search_results"] = result_list
        details["brand_rank_position"] = brand_rank
        details["brand_results_count"] = brand_count

        # Site index check (uses 1 more API call)
        try:
            site_resp = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": api_key, "cx": cx, "q": f"site:{domain}", "num": 10},
                timeout=10,
            )
            if site_resp.status_code == 200:
                site_data = site_resp.json()
                # Use totalResults from search info
                total_str = site_data.get("searchInformation", {}).get("totalResults", "0")
                details["site_index_estimate"] = min(int(total_str), 10000)
        except Exception as exc:
            logger.warning("CSE site: query failed: %s", exc)

        return _compute_score(details)

    except Exception as exc:
        logger.warning("Google CSE API check failed: %s", exc)
        return None


def _is_brand_domain(result_domain: str, domain: str) -> bool:
    """Whether a search result belongs to the brand's own domain."""
    if not result_domain or not domain:
        return False
    return result_domain == domain or result_domain.endswith("." + domain) or domain.endswith("." + result_domain)


def _check_via_serper(brand_name: str, domain: str) -> tuple[float, dict] | None:
    """Measure Google visibility from real search results via Serper.

    Returns ``None`` when Serper is unconfigured or the call fails, so the caller
    falls through to the next strategy. Every field is observed: the Knowledge
    Panel comes from Google's own knowledgeGraph block, and the indexed-page
    count is what a ``site:`` query actually returns.
    """
    from apps.analyzer.pipeline import serper

    if not serper.is_configured():
        return None

    data = serper.search(brand_name, num=10)
    if data is None:
        return None

    details = {
        "method": "serper",
        "brand_search_results": [],
        "site_index_estimate": 0,
        "brand_rank_position": None,
        "brand_results_count": 0,
        "total_results_checked": 0,
    }

    organic = data.get("organic") or []
    details["total_results_checked"] = len(organic)

    brand_rank = None
    brand_count = 0
    for i, item in enumerate(organic):
        url = item.get("link", "") or ""
        result_domain = urlparse(url).netloc.lower().replace("www.", "")
        is_brand = _is_brand_domain(result_domain, domain)
        details["brand_search_results"].append(
            {
                "position": i + 1,
                "url": url[:200],
                "title": (item.get("title") or "")[:100],
                "snippet": (item.get("snippet") or "")[:200],
                "is_brand": is_brand,
            }
        )
        if is_brand:
            brand_count += 1
            if brand_rank is None:
                brand_rank = i + 1

    details["brand_rank_position"] = brand_rank
    details["brand_results_count"] = brand_count
    details["has_knowledge_panel"] = bool((data.get("knowledgeGraph") or {}).get("title"))

    # Indexed pages. Serper returns no result total, so this is a floor built from
    # the distinct URLs a site: query actually surfaces, not an estimate.
    site_data = serper.search(f"site:{domain}", num=_SITE_INDEX_SAMPLE)
    if site_data is not None:
        indexed = {
            (item.get("link") or "").split("?")[0]
            for item in (site_data.get("organic") or [])
            if item.get("link")
        }
        details["site_index_estimate"] = len(indexed)
        details["site_index_is_floor"] = True

    return _compute_score(details)


def _check_via_scraper(brand_name: str, domain: str) -> tuple[float, dict] | None:
    """Use googlesearch-python scraper (fallback, often blocked)."""
    try:
        from googlesearch import search as google_search
    except ImportError:
        return None

    details = {
        "method": "googlesearch_scraper",
        "brand_search_results": [],
        "site_index_estimate": 0,
        "brand_rank_position": None,
        "brand_results_count": 0,
        "total_results_checked": 0,
    }

    try:
        brand_results = list(google_search(brand_name, num_results=20, lang="en"))

        # If we got 0 results, scraper was likely blocked
        if not brand_results:
            logger.info("googlesearch returned 0 results (likely blocked), falling back")
            return None

        details["total_results_checked"] = len(brand_results)

        brand_rank = None
        brand_count = 0
        result_list = []

        for i, url in enumerate(brand_results):
            result_domain = urlparse(url).netloc.replace("www.", "")
            is_brand = domain in result_domain or result_domain in domain
            result_list.append({
                "position": i + 1,
                "url": url[:200],
                "is_brand": is_brand,
            })
            if is_brand:
                brand_count += 1
                if brand_rank is None:
                    brand_rank = i + 1

        details["brand_search_results"] = result_list[:10]
        details["brand_rank_position"] = brand_rank
        details["brand_results_count"] = brand_count

        # Site index query
        try:
            site_results = list(google_search(f"site:{domain}", num_results=20, lang="en"))
            details["site_index_estimate"] = len(site_results)
        except Exception:
            details["site_index_estimate"] = 0

        return _compute_score(details)

    except Exception as exc:
        logger.warning("Google scraper failed: %s (falling back)", exc)
        return None


def _compute_score(details: dict) -> tuple[float, dict]:
    """Compute the Google visibility score from collected details."""
    # Brand search rank (40%): #1 = 100, #2 = 90, #3 = 80, ... not found = 0
    if details.get("brand_rank_position"):
        rank_score = max(0, 100 - (details["brand_rank_position"] - 1) * 10)
    else:
        rank_score = 0

    # Site index (30%): 20+ = 100, scale linearly
    index_count = details.get("site_index_estimate", 0)
    if isinstance(index_count, int) and index_count > 20:
        # For API results with large numbers, use log scale
        import math
        index_score = min(100, 50 + math.log10(max(index_count, 1)) * 15)
    else:
        index_score = min(100, (index_count / 20) * 100)

    # Brand dominance (30%): % of results that are the brand * 100
    total_checked = details.get("total_results_checked") or 1
    dominance_score = (details.get("brand_results_count", 0) / total_checked) * 100

    score = (rank_score * 0.40) + (index_score * 0.30) + (dominance_score * 0.30)

    # A Knowledge Panel is a real visibility signal, but only when we actually saw
    # one. Strategies that cannot observe it never set the flag, so they are unaffected.
    if details.get("has_knowledge_panel"):
        score = min(100, score + 5)

    details["sub_scores"] = {
        "brand_search_rank": round(rank_score, 1),
        "site_index": round(index_score, 1),
        "brand_dominance": round(dominance_score, 1),
    }

    return round(min(100, max(0, score)), 1), details
