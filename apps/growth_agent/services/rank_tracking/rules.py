"""
Rank tracking, pure: which keywords to track, where a domain sits in a list of
Google results, and the weekly summaries the panel shows. No network, no database.
"""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlparse

MAX_KEYWORDS = 25
MAX_KEYWORD_CHARS = 120
MAX_PROMPT_WORDS = 7
TOP_TEN = 10
WEEKS_SHOWN = 8
NOT_RANKED = None


def week_of(day: date) -> date:
    """Monday of the week ``day`` falls in."""
    return day - timedelta(days=day.weekday())


def clean(keyword: str) -> str:
    return " ".join((keyword or "").lower().split())[:MAX_KEYWORD_CHARS]


def pick_keywords(
    *, brand_rows: list[dict], gap_rows: list[dict], prompts: list[str], taken: set[str], room: int
) -> list[str]:
    """New keywords to start tracking, best first: the brand's own keywords (in traffic order),
    then the biggest gaps, then short AI prompts. ``taken`` holds every keyword already known,
    including ones the owner removed, so those are never added back."""
    short_prompts = [p for p in prompts if 0 < len(p.split()) <= MAX_PROMPT_WORDS]
    candidates = [r.get("keyword", "") for r in brand_rows]
    candidates += [
        g.get("keyword", "") for g in sorted(gap_rows, key=lambda g: -int(g.get("search_volume") or 0))
    ]
    candidates += short_prompts
    picked: list[str] = []
    for raw in candidates:
        keyword = clean(raw)
        if keyword and keyword not in taken and keyword not in picked:
            picked.append(keyword)
        if len(picked) >= room:
            break
    return picked


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def belongs(host: str, domain: str) -> bool:
    return bool(domain) and (host == domain or host.endswith(f".{domain}"))


def positions(organic: list[dict], domains: list[str]) -> dict[str, int | None]:
    """Each domain's best position in Google's organic results (1-based), None when absent."""
    found: dict[str, int | None] = dict.fromkeys(domains, NOT_RANKED)
    for index, item in enumerate(organic or [], start=1):
        position = int(item.get("position") or index)
        host = _host(item.get("link") or "")
        for domain in domains:
            if found[domain] is None and belongs(host, domain):
                found[domain] = position
    return found


def seo_week_summary(rows: list[dict], domains: list[str]) -> dict[str, dict]:
    """Per domain for one week: keywords in the top 10, and average position over keywords it ranks for."""
    out = {}
    for domain in domains:
        ranked = [r["position"] for r in rows if r["domain"] == domain and r["position"]]
        out[domain] = {
            "top10": sum(1 for p in ranked if p <= TOP_TEN),
            "ranked": len(ranked),
            "average": round(sum(ranked) / len(ranked), 1) if ranked else None,
        }
    return out
