"""
Competitors found on Google, for brands whose site analysis found none.

The rank check already searches Google for each tracked keyword. The sites that
keep showing up in the top 10 across those keywords are the brand's real search
competitors. Marketplaces, encyclopedias, social networks and Q&A sites rank for
everything, so they never count. Pure: no network, no database.
"""

from __future__ import annotations

from collections import Counter

from ..keywords.gaps import Rival
from .rules import TOP_TEN, _host, belongs

MAX_RIVALS = 3
MIN_KEYWORDS = 2  # a site in the top 10 for just one keyword is a coincidence, not a rival

# Sites that rank for almost any search: never a brand's competitor.
PLATFORMS = (
    "amazon.",
    "flipkart.com",
    "ebay.",
    "aliexpress.com",
    "alibaba.com",
    "indiamart.com",
    "etsy.com",
    "walmart.com",
    "meesho.com",
    "wikipedia.org",
    "wikihow.com",
    "youtube.com",
    "reddit.com",
    "quora.com",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "pinterest.",
    "x.com",
    "twitter.com",
    "tiktok.com",
    "medium.com",
    "google.",
    "apple.com",
    "github.com",
    "stackoverflow.com",
    "yelp.",
    "tripadvisor.",
    "justdial.com",
    "glassdoor.",
    "indeed.",
    "forbes.com",
    "nytimes.com",
)


def _is_platform(host: str) -> bool:
    """An entry like "amazon." matches that name under any TLD ("amazon.in", "smile.amazon.com");
    "youtube.com" matches the domain and its subdomains only (never "youtube.com.evil" or "x.company.com")."""
    dotted = f".{host}"
    for p in PLATFORMS:
        if p.endswith("."):
            if f".{p}" in dotted:
                return True
        elif host == p or host.endswith(f".{p}"):
            return True
    return False


def _name(domain: str) -> str:
    """A readable name from a domain: "makerbot.com" -> "Makerbot"."""
    return domain.split(".")[0].replace("-", " ").title()


def pick(serps: dict[str, list[dict]], *, own_domain: str, limit: int = MAX_RIVALS) -> list[Rival]:
    """The sites in Google's top 10 for the most tracked keywords (at least two),
    best average position breaking ties. ``serps`` is keyword -> organic results."""
    seen: Counter[str] = Counter()
    positions: dict[str, list[int]] = {}
    for organic in serps.values():
        counted: set[str] = set()
        for index, item in enumerate((organic or [])[:TOP_TEN], start=1):
            host = _host(item.get("link") or "")
            if not host or host in counted or belongs(host, own_domain) or _is_platform(host):
                continue
            counted.add(host)
            seen[host] += 1
            positions.setdefault(host, []).append(int(item.get("position") or index))
    ranked = sorted(
        (h for h, n in seen.items() if n >= MIN_KEYWORDS),
        key=lambda h: (-seen[h], sum(positions[h]) / len(positions[h])),
    )
    return [Rival(_name(h), h) for h in ranked[:limit]]
