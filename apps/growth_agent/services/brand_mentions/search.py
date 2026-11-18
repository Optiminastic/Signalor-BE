"""
Find pages from the past week that talk about a brand, outside its own site:
Google web results, Google News and Reddit, through Serper.

Returns candidate mentions only; whether each is really about the brand (and
its sentiment) is decided by ``classify``. Never raises: a failed search gives
no candidates, never invented ones.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from core.llm import serper

logger = logging.getLogger("apps")

WEB_URL = "https://google.serper.dev/search"
NEWS_URL = "https://google.serper.dev/news"
TIMEOUT_SECONDS = 15
PAST_WEEK = "qdr:w"
RESULTS = 20


@dataclass(frozen=True)
class Candidate:
    url: str
    title: str
    snippet: str
    source: str
    channel: str  # web | news | reddit
    published: str


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _post(url: str, query: str) -> dict:
    key = serper.api_key()
    if not key:
        return {}
    try:
        reply = requests.post(
            url,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": query, "tbs": PAST_WEEK, "num": RESULTS},
            timeout=TIMEOUT_SECONDS,
        )
        return reply.json() if reply.ok else {}
    except (requests.RequestException, ValueError):
        logger.warning("brand mentions: search failed for one query")
        return {}


def _rows(items: list[dict], channel: str) -> list[Candidate]:
    out = []
    for item in items or []:
        url = item.get("link") or ""
        if not url.startswith("http"):
            continue
        out.append(
            Candidate(
                url=url[:1000],
                title=(item.get("title") or "")[:300],
                snippet=item.get("snippet") or "",
                source=host(url),
                channel=channel,
                published=(item.get("date") or "")[:40],
            )
        )
    return out


def _channel(row: Candidate) -> Candidate:
    """Reddit results found by the web search count as Reddit."""
    if row.source == "reddit.com" or row.source.endswith(".reddit.com"):
        return Candidate(row.url, row.title, row.snippet, "reddit.com", "reddit", row.published)
    return row


def find(name: str, domain: str) -> list[Candidate]:
    """Pages from the past week naming ``name``, not on ``domain`` itself (deduplicated by URL).

    Plain queries only: Serper's free plan refuses quotes and ``site:`` operators, so the
    brand's own site is dropped here and pages not really about the brand by ``classify``.
    """
    found = (
        _rows(_post(WEB_URL, name).get("organic"), "web")
        + _rows(_post(NEWS_URL, name).get("news"), "news")
        + _rows(_post(WEB_URL, f"{name} reddit").get("organic"), "web")
    )
    seen: set[str] = set()
    unique = []
    for row in map(_channel, found):
        own = domain and (row.source == domain or row.source.endswith(f".{domain}"))
        if row.url not in seen and not own:
            seen.add(row.url)
            unique.append(row)
    return unique
