"""Medium Presence check (score 0-100)."""

import logging
import re

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("apps")

MEDIUM_SEARCH_URL = "https://medium.com/search"


def check_medium(brand_name: str) -> tuple[float, dict]:
    """
    Check Medium presence for a brand.
    Returns (score, details_dict).

    Sub-scores:
      - article_volume (60%): number of articles mentioning the brand
      - title_relevance (40%): how many article titles contain the brand name
    """
    details = {
        "articles": [],
        "total_articles": 0,
        "relevant_titles": 0,
    }

    try:
        resp = requests.get(
            MEDIUM_SEARCH_URL,
            params={"q": brand_name},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html",
            },
            timeout=15,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Look for article cards — Medium uses h2/h3 tags for titles
        articles = []
        brand_lower = brand_name.lower()

        # Try multiple selectors for article titles
        title_elements = soup.select("h2, h3, article h2, article h3")
        seen_titles = set()

        for el in title_elements:
            title = el.get_text(strip=True)
            if not title or len(title) < 10 or title in seen_titles:
                continue
            seen_titles.add(title)

            # Find parent link if available
            link = el.find_parent("a")
            href = link.get("href", "") if link else ""
            if href and not href.startswith("http"):
                href = f"https://medium.com{href}"

            is_relevant = brand_lower in title.lower()

            articles.append({
                "title": title[:200],
                "url": href[:300] if href else "",
                "is_relevant": is_relevant,
            })

        details["articles"] = articles[:20]  # cap at 20
        details["total_articles"] = len(articles)
        details["relevant_titles"] = sum(1 for a in articles if a["is_relevant"])

    except Exception as exc:
        logger.warning("Medium scrape failed: %s", exc)
        # Fallback to LLM estimation
        return _llm_fallback(brand_name, details)

    # If no articles found (JS rendering issue), try LLM fallback
    if details["total_articles"] == 0:
        return _llm_fallback(brand_name, details)

    # Score calculation
    article_count = details["total_articles"]
    relevant = details["relevant_titles"]

    # Article volume (60%): 15+ articles = 100
    volume_score = min(100, (article_count / 15) * 100)

    # Title relevance (40%): % of titles containing brand name
    relevance_score = (relevant / max(article_count, 1)) * 100

    score = volume_score * 0.60 + relevance_score * 0.40

    details["sub_scores"] = {
        "article_volume": round(volume_score, 1),
        "title_relevance": round(relevance_score, 1),
    }

    return round(min(100, max(0, score)), 1), details


def _llm_fallback(brand_name: str, details: dict) -> tuple[float, dict]:
    """Use LLM to estimate Medium presence when scraping fails."""
    try:
        from apps.analyzer.pipeline.llm import ask_llm

        prompt = (
            f"Estimate the Medium.com presence score (0-100) for the brand "
            f"'{brand_name}'. Consider: Are there articles about this brand on Medium? "
            f"Is the brand likely writing on Medium? "
            f"Reply with ONLY a JSON object: "
            f'{{"score": <number>, "reasoning": "<brief explanation>"}}'
        )
        response = ask_llm(prompt, purpose="medium_presence_estimate")

        import json
        try:
            data = json.loads(response)
            score = float(data.get("score", 15))
            details["method"] = "llm_estimate"
            details["reasoning"] = data.get("reasoning", "")
            return round(min(100, max(0, score)), 1), details
        except (json.JSONDecodeError, ValueError):
            pass
    except ImportError:
        pass

    details["method"] = "fallback_default"
    return 15.0, details
