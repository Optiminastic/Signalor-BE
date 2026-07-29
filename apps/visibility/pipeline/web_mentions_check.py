"""Web Mentions check — finds brand mentions across blogs, news, forums, etc.

Every mention reported here is a URL a search engine actually returned. There is
deliberately no LLM fallback: this check used to ask a model to "include up to 15
specific, realistic mentions", which is a request for plausible fiction — the
model cannot know where a brand is mentioned, so it produced invented URLs that
were then stored and scored as measured presence.

Strategy 1: Google Custom Search API (if GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX set)
Strategy 2: Serper (Google Search API) — the maintained path, shares SERPER_API_KEY

When neither can answer the result is *unknown*: score 0 with ``unknown=True``,
never a guess.

Returns (score 0-100, details_dict).
"""

import logging
import os
from urllib.parse import urlparse

import requests

logger = logging.getLogger("apps")

# Domain → platform type mapping
PLATFORM_DOMAINS = {
    # Blogs
    "wordpress.com": "blog",
    "substack.com": "blog",
    "ghost.io": "blog",
    "dev.to": "blog",
    "hashnode.dev": "blog",
    "blogspot.com": "blog",
    "hubspot.com": "blog",
    "wix.com": "blog",
    # News
    "techcrunch.com": "news",
    "forbes.com": "news",
    "bloomberg.com": "news",
    "bbc.com": "news",
    "bbc.co.uk": "news",
    "cnn.com": "news",
    "reuters.com": "news",
    "theverge.com": "news",
    "wired.com": "news",
    "arstechnica.com": "news",
    "venturebeat.com": "news",
    "zdnet.com": "news",
    "engadget.com": "news",
    "mashable.com": "news",
    "businessinsider.com": "news",
    "nytimes.com": "news",
    "wsj.com": "news",
    "theguardian.com": "news",
    "cnbc.com": "news",
    # Forums
    "news.ycombinator.com": "forum",
    "stackoverflow.com": "forum",
    "stackexchange.com": "forum",
    "quora.com": "forum",
    "discourse.org": "forum",
    # Social
    "linkedin.com": "social",
    "twitter.com": "social",
    "x.com": "social",
    "youtube.com": "social",
    "facebook.com": "social",
    "instagram.com": "social",
    "pinterest.com": "social",
    "tiktok.com": "social",
    # Reviews
    "g2.com": "review",
    "trustpilot.com": "review",
    "capterra.com": "review",
    "producthunt.com": "review",
    "getapp.com": "review",
    "softwareadvice.com": "review",
    "glassdoor.com": "review",
    "yelp.com": "review",
    "tripadvisor.com": "review",
}

# Organic results per Serper query, and the cap on stored mentions. The scoring
# curve saturates at 20 mentions, so collecting more would not change the score.
_MENTIONS_PER_QUERY = 20
_MAX_MENTIONS = 20

# Authority weights for scoring
TYPE_AUTHORITY = {
    "news": 1.0,
    "review": 0.85,
    "blog": 0.7,
    "forum": 0.6,
    "social": 0.5,
    "other": 0.4,
}


def _classify_domain(domain: str) -> str:
    """Classify a domain into a platform type."""
    domain_lower = domain.lower().replace("www.", "")
    # Exact match
    if domain_lower in PLATFORM_DOMAINS:
        return PLATFORM_DOMAINS[domain_lower]
    # Partial match (e.g., subdomain.wordpress.com)
    for known, ptype in PLATFORM_DOMAINS.items():
        if domain_lower.endswith("." + known) or known.endswith("." + domain_lower):
            return ptype
    return "other"


def check_web_mentions(brand_name: str, brand_url: str) -> tuple[float, dict]:
    """
    Check web mentions for a brand across blogs, news, forums, etc.
    Returns (score, details_dict).
    """
    brand_domain = urlparse(brand_url).netloc.replace("www.", "").lower()

    # Strategy 1: Google CSE API
    api_key = os.environ.get("GOOGLE_CSE_API_KEY", "")
    cse_cx = os.environ.get("GOOGLE_CSE_CX", "")
    if api_key and cse_cx:
        result = _check_via_cse(brand_name, brand_domain, api_key, cse_cx)
        if result is not None:
            return result

    # Strategy 2: Serper
    result = _check_via_serper(brand_name, brand_domain)
    if result is not None:
        return result

    logger.warning("Web mentions for %s: no search backend available (result: unknown)", brand_name)
    return 0.0, {
        "method": "unavailable",
        "unknown": True,
        "mentions": [],
        "total_mentions": 0,
        "platform_counts": {},
        "sub_scores": {},
        "error": "No search backend configured (set SERPER_API_KEY or GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX).",
    }


def _check_via_serper(brand_name: str, brand_domain: str) -> tuple[float, dict] | None:
    """Find real third-party mentions of the brand via Serper.

    Returns ``None`` when Serper is unconfigured or every query fails, so the
    caller can report *unknown* rather than an empty-looking zero.
    """
    from apps.analyzer.pipeline import serper

    if not serper.is_configured():
        return None

    queries = [
        f'"{brand_name}" -site:reddit.com -site:{brand_domain}',
        f'"{brand_name}" review',
    ]

    mentions: list[dict] = []
    seen_urls: set[str] = set()
    answered = False
    for query in queries:
        data = serper.search(query, num=_MENTIONS_PER_QUERY)
        if data is None:
            continue
        answered = True
        for item in data.get("organic") or []:
            url = (item.get("link") or "")[:300]
            if not url or url in seen_urls:
                continue
            domain = urlparse(url).netloc.replace("www.", "").lower()
            if not domain:
                continue
            # Reddit has its own dedicated check; the brand's own site is not a mention.
            if domain.endswith("reddit.com"):
                continue
            if brand_domain and (domain == brand_domain or domain.endswith("." + brand_domain)):
                continue
            seen_urls.add(url)
            mentions.append(
                {
                    "url": url,
                    "title": (item.get("title") or "")[:150],
                    "snippet": (item.get("snippet") or "")[:250],
                    "platform_type": _classify_domain(domain),
                    "domain": domain,
                }
            )

    if not answered:
        return None
    return _compute_score(mentions[:_MAX_MENTIONS], method="serper")


def _check_via_cse(
    brand_name: str, brand_domain: str, api_key: str, cx: str
) -> tuple[float, dict] | None:
    """Use Google CSE to find web mentions (excludes Reddit/own domain)."""
    try:
        query = f'"{brand_name}" -site:reddit.com -site:{brand_domain}'
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": api_key, "cx": cx, "q": query, "num": 10},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("Web mentions CSE API returned %d", resp.status_code)
            return None

        data = resp.json()
        items = data.get("items", [])

        # Try a second page for more results
        mentions = []
        for item in items:
            url = item.get("link", "")
            domain = urlparse(url).netloc.replace("www.", "").lower()
            if brand_domain in domain or domain in brand_domain:
                continue  # Skip brand's own domain
            mentions.append({
                "url": url[:300],
                "title": item.get("title", "")[:150],
                "snippet": item.get("snippet", "")[:250],
                "platform_type": _classify_domain(domain),
                "domain": domain,
            })

        # Second query for more diversity (site-specific)
        try:
            resp2 = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": api_key, "cx": cx,
                    "q": f'"{brand_name}"',
                    "num": 10, "start": 11,
                },
                timeout=10,
            )
            if resp2.status_code == 200:
                for item in resp2.json().get("items", []):
                    url = item.get("link", "")
                    domain = urlparse(url).netloc.replace("www.", "").lower()
                    if brand_domain in domain or domain in brand_domain:
                        continue
                    if any(m["url"] == url[:300] for m in mentions):
                        continue
                    mentions.append({
                        "url": url[:300],
                        "title": item.get("title", "")[:150],
                        "snippet": item.get("snippet", "")[:250],
                        "platform_type": _classify_domain(domain),
                        "domain": domain,
                    })
        except Exception as exc:
            logger.debug("Web mentions second page failed: %s", exc)

        mentions = mentions[:20]
        return _compute_score(mentions, method="google_cse_api")

    except Exception as exc:
        logger.warning("Web mentions CSE check failed: %s", exc)
        return None


def _compute_score(mentions: list[dict], method: str) -> tuple[float, dict]:
    """Compute the web mentions score from collected mentions."""
    total = len(mentions)

    # Platform counts
    platform_counts: dict[str, int] = {}
    for m in mentions:
        pt = m.get("platform_type", "other")
        platform_counts[pt] = platform_counts.get(pt, 0) + 1

    unique_types = len(platform_counts)

    # Sub-score: mention_volume (40%) — 20+ mentions = 100
    volume_score = min(100, (total / 20) * 100)

    # Sub-score: platform_diversity (35%) — 5+ unique types = 100
    diversity_score = min(100, (unique_types / 5) * 100)

    # Sub-score: source_authority (25%) — weighted by platform type
    if total > 0:
        authority_sum = sum(
            TYPE_AUTHORITY.get(m.get("platform_type", "other"), 0.4)
            for m in mentions
        )
        authority_score = min(100, (authority_sum / total) * 100)
    else:
        authority_score = 0

    overall = (
        volume_score * 0.40
        + diversity_score * 0.35
        + authority_score * 0.25
    )

    details = {
        "method": method,
        "mentions": mentions,
        "total_mentions": total,
        "platform_counts": platform_counts,
        "sub_scores": {
            "mention_volume": round(volume_score, 1),
            "platform_diversity": round(diversity_score, 1),
            "source_authority": round(authority_score, 1),
        },
    }

    return round(min(100, max(0, overall)), 1), details
