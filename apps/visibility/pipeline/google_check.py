"""Google Search Visibility check (score 0-100).

Uses a multi-strategy approach:
  1. Google Custom Search API (if GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX configured)
  2. googlesearch-python scraper (legacy, often blocked)
  3. LLM estimation fallback (always works)
"""

import json
import logging
import os
import re
from urllib.parse import urlparse, quote_plus

import requests

logger = logging.getLogger("apps")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


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

    # Strategy 1: Google Custom Search API (most reliable)
    api_key = os.environ.get("GOOGLE_CSE_API_KEY", "")
    cse_cx = os.environ.get("GOOGLE_CSE_CX", "")
    if api_key and cse_cx:
        result = _check_via_cse_api(brand_name, domain, api_key, cse_cx)
        if result is not None:
            return result

    # Strategy 2: googlesearch-python (often blocked by Google)
    result = _check_via_scraper(brand_name, domain)
    if result is not None:
        return result

    # Strategy 3: LLM estimation (always works if LLM available)
    return _llm_google_check(brand_name, brand_url, domain)


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


def _llm_google_check(brand_name: str, brand_url: str, domain: str) -> tuple[float, dict]:
    """
    Use LLM to estimate Google visibility — comprehensive check.
    This is the most reliable fallback since it uses Gemini's knowledge.
    """
    details = {
        "method": "llm_analysis",
        "brand_search_results": [],
        "site_index_estimate": 0,
        "brand_rank_position": None,
        "brand_results_count": 0,
        "total_results_checked": 0,
    }

    try:
        from apps.analyzer.pipeline.llm import is_available, ask_llm

        if not is_available():
            return 20.0, {**details, "error": "No LLM available"}

        prompt = (
            f"Analyze the Google Search visibility for the brand '{brand_name}' "
            f"with website '{brand_url}' (domain: {domain}).\n\n"
            f"Evaluate these specific factors:\n"
            f"1. Brand Search Rank: If someone Googles '{brand_name}', does the "
            f"brand's own website ({domain}) appear? What position (1-10)?\n"
            f"2. Indexed Pages: Approximately how many pages from {domain} does "
            f"Google have indexed? (estimate)\n"
            f"3. Brand Dominance: When searching '{brand_name}', what percentage "
            f"of the top 10 results are from {domain} vs other sites?\n"
            f"4. Featured Snippets/Knowledge Panel: Does this brand likely have "
            f"a Google Knowledge Panel or appear in featured snippets?\n"
            f"5. Google AI Overview: Would this brand likely appear in Google's "
            f"AI Overview for industry-related searches?\n\n"
            f"Reply with ONLY this JSON:\n"
            f"{{\n"
            f'  "brand_rank_position": <1-10 or null if not in top 10>,\n'
            f'  "estimated_indexed_pages": <number>,\n'
            f'  "brand_results_in_top10": <0-10>,\n'
            f'  "has_knowledge_panel": <true/false>,\n'
            f'  "in_ai_overview": <true/false>,\n'
            f'  "brand_search_rank_score": <0-100>,\n'
            f'  "site_index_score": <0-100>,\n'
            f'  "brand_dominance_score": <0-100>,\n'
            f'  "top_results": [\n'
            f'    {{"position": 1, "description": "result description", "is_brand": true/false}}\n'
            f"  ],\n"
            f'  "reasoning": "brief explanation"\n'
            f"}}"
        )

        response = ask_llm(
            prompt, preferred_provider="gemini", max_tokens=1024,
            purpose="Google Visibility Analysis",
        )

        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            data = json.loads(match.group())

            # Extract structured data
            rank_pos = data.get("brand_rank_position")
            indexed = data.get("estimated_indexed_pages", 0)
            brand_in_top10 = data.get("brand_results_in_top10", 0)

            details["brand_rank_position"] = rank_pos
            details["site_index_estimate"] = indexed
            details["brand_results_count"] = brand_in_top10
            details["total_results_checked"] = 10
            details["has_knowledge_panel"] = data.get("has_knowledge_panel", False)
            details["in_ai_overview"] = data.get("in_ai_overview", False)
            details["reasoning"] = data.get("reasoning", "")

            # Build top results from LLM response
            top_results = data.get("top_results", [])
            for r in top_results[:10]:
                details["brand_search_results"].append({
                    "position": r.get("position", 0),
                    "url": r.get("url", r.get("description", ""))[:200],
                    "is_brand": r.get("is_brand", False),
                })

            # Use LLM-provided sub-scores if available
            rank_score = data.get("brand_search_rank_score")
            index_score = data.get("site_index_score")
            dominance_score = data.get("brand_dominance_score")

            if rank_score is not None and index_score is not None and dominance_score is not None:
                rank_score = min(100, max(0, float(rank_score)))
                index_score = min(100, max(0, float(index_score)))
                dominance_score = min(100, max(0, float(dominance_score)))

                score = (rank_score * 0.40) + (index_score * 0.30) + (dominance_score * 0.30)

                details["sub_scores"] = {
                    "brand_search_rank": round(rank_score, 1),
                    "site_index": round(index_score, 1),
                    "brand_dominance": round(dominance_score, 1),
                }

                # Bonus for knowledge panel / AI overview
                if details.get("has_knowledge_panel"):
                    score = min(100, score + 5)
                if details.get("in_ai_overview"):
                    score = min(100, score + 5)

                return round(min(100, max(0, score)), 1), details

            # Fallback: compute from extracted values
            return _compute_score(details)

    except Exception as exc:
        logger.warning("LLM Google check failed: %s", exc)

    return 20.0, {**details, "error": "All methods failed"}


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

    details["sub_scores"] = {
        "brand_search_rank": round(rank_score, 1),
        "site_index": round(index_score, 1),
        "brand_dominance": round(dominance_score, 1),
    }

    return round(min(100, max(0, score)), 1), details
