"""
Find a brand's social profiles through Google search (Serper), for when the site
analysis couldn't read them from the brand's homepage (a site that blocks bots,
or no links on it). Also used for competitors.

One search per platform, ``"<brand>" <platform>``. A result counts only if its
address is that platform's profile page (a company page, handle or channel), not
a post, an article or a search page. Never raises; nothing found -> no profile.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from core.llm import serper

SEARCH_RESULTS = 10
# platform -> (search word, profile address pattern)
PROFILE_PATTERNS: dict[str, tuple[str, re.Pattern[str]]] = {
    "linkedin": ("linkedin", re.compile(r"^(?:[a-z]{2,3}\.)?linkedin\.com/(company|school)/[^/?#]+/?$")),
    "x": (
        "twitter",
        re.compile(r"^(?:x|twitter)\.com/(?!search|hashtag|i/|intent|share|home)[A-Za-z0-9_]{1,15}/?$"),
    ),
    "youtube": ("youtube", re.compile(r"^youtube\.com/(@[^/?#]+|c/[^/?#]+|channel/[^/?#]+|user/[^/?#]+)/?$")),
    "instagram": (
        "instagram",
        re.compile(r"^instagram\.com/(?!p/|reel/|explore/|stories/|accounts/)[A-Za-z0-9_.]{1,30}/?$"),
    ),
}
_WORDS = re.compile(r"[a-z0-9]+")


def _address(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    return f"{host}{parsed.path}".rstrip("/")


def _mentions_brand(item: dict, tokens: set[str]) -> bool:
    text = f"{item.get('title', '')} {item.get('link', '')}".lower()
    return bool(tokens & set(_WORDS.findall(text))) if tokens else True


def _brand_tokens(name: str, domain: str) -> set[str]:
    words = set(_WORDS.findall(name.lower()))
    stem = domain.lower().removeprefix("www.").split(".")[0]
    return {w for w in words | {stem} if len(w) >= 3}


def find_profile(name: str, domain: str, platform: str) -> str:
    """The brand's profile address on ``platform``, or "" when none is found."""
    word, pattern = PROFILE_PATTERNS[platform]
    reply = serper.search(f'"{name}" {word}', num=SEARCH_RESULTS) or {}
    tokens = _brand_tokens(name, domain)
    for item in reply.get("organic", []) or []:
        link = item.get("link") or ""
        if pattern.match(_address(link)) and _mentions_brand(item, tokens):
            return link
    return ""


def find_profiles(name: str, domain: str) -> dict[str, str]:
    """platform -> profile address, for each platform where one was found."""
    if not name.strip() or not serper.is_configured():
        return {}
    found = {platform: find_profile(name, domain, platform) for platform in PROFILE_PATTERNS}
    return {platform: url for platform, url in found.items() if url}
