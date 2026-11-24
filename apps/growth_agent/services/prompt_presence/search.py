"""
Where a prompt is being discussed: one Google search per platform, read into rows.
Pure except ``search_prompt``, which calls Serper.

- Google: the top web results for the prompt itself.
- Reddit: threads (``reddit.com/r/<sub>/comments/...``), with their subreddit.
- LinkedIn: posts and articles (``linkedin.com/posts/...``, ``/pulse/...``).
- X: posts (``x.com/<user>/status/...``, or twitter.com).

Profile pages, search pages and other non-post URLs are dropped, so every row is
a conversation the owner could read or join.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.llm import serper

from ..positioning.inputs import bare_domain

RESULTS_PER_PLATFORM = 5
SEARCH_DEPTH = 10

QUERIES = {
    "google": "{prompt}",
    "reddit": "{prompt} site:reddit.com",
    "linkedin": "{prompt} (site:linkedin.com/posts OR site:linkedin.com/pulse)",
    "x": "{prompt} (site:x.com OR site:twitter.com)",
}

_REDDIT_THREAD = re.compile(r"reddit\.com/r/([A-Za-z0-9_]+)/comments/", re.I)
_LINKEDIN_POST = re.compile(r"linkedin\.com/(posts|pulse)/", re.I)
_X_POST = re.compile(r"(?:^|\.|//)(?:x|twitter)\.com/[A-Za-z0-9_]+/status/\d+", re.I)


@dataclass(frozen=True)
class Result:
    platform: str
    url: str
    title: str
    snippet: str
    community: str
    position: int


def _keep(platform: str, url: str) -> tuple[bool, str]:
    """(is a post on this platform, its community)."""
    if platform == "reddit":
        match = _REDDIT_THREAD.search(url)
        return (True, f"r/{match.group(1)}") if match else (False, "")
    if platform == "linkedin":
        return bool(_LINKEDIN_POST.search(url)), ""
    if platform == "x":
        return bool(_X_POST.search(url)), ""
    return True, ""


def read(platform: str, organic: list[dict]) -> list[Result]:
    """The platform's posts from one search's organic results, best first. Pure."""
    out: list[Result] = []
    for index, item in enumerate(organic or [], start=1):
        url = item.get("link") or ""
        keep, community = _keep(platform, url)
        if not url or not keep:
            continue
        out.append(
            Result(
                platform=platform,
                url=url,
                title=(item.get("title") or "")[:300],
                snippet=item.get("snippet") or "",
                community=community,
                position=int(item.get("position") or index),
            )
        )
        if len(out) >= RESULTS_PER_PLATFORM:
            break
    return out


def mentions_brand(result: Result, *, name: str, domain: str) -> bool:
    """The result names the brand or links to its site."""
    if domain and bare_domain(result.url).endswith(domain):
        return True
    words = f"{result.title} {result.snippet}".lower()
    return bool(name) and len(name) >= 3 and re.search(rf"\b{re.escape(name.lower())}\b", words) is not None


def search_prompt(prompt: str) -> dict[str, list[Result]] | None:
    """Every platform's posts for one prompt. None when search isn't available. Network."""
    if not serper.is_configured():
        return None
    found: dict[str, list[Result]] = {}
    for platform, template in QUERIES.items():
        data = serper.search(template.format(prompt=prompt), num=SEARCH_DEPTH)
        if data is None:
            continue  # unknown for this platform: no rows rather than wrong ones
        found[platform] = read(platform, data.get("organic") or [])
    return found
