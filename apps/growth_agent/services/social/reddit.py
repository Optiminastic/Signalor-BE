"""Brand mentions on Reddit in the last 90 days, from Reddit's public search API.

Needs a Reddit app (``REDDIT_CLIENT_ID`` / ``REDDIT_CLIENT_SECRET``); uses the
app-only token, so nothing is posted and no user account is involved.
Docs: https://www.reddit.com/dev/api/#GET_search
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

import requests
from django.utils import timezone

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
SEARCH_URL = "https://oauth.reddit.com/search"
USER_AGENT = "signalor.ai/1.0 (brand mention research)"
TIMEOUT_SECONDS = 15
WINDOW_DAYS = 90
TOP_COMMUNITIES = 5
SAMPLE_POSTS = 5


class RedditError(Exception):
    pass


def _token(client_id: str, client_secret: str) -> str:
    try:
        reply = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RedditError(f"Reddit couldn't be reached: {type(exc).__name__}") from exc
    if not reply.ok or not reply.json().get("access_token"):
        raise RedditError(f"Reddit refused the app credentials (HTTP {reply.status_code})")
    return reply.json()["access_token"]


def summarize(posts: list[dict], now=None) -> dict:
    """Mentions in the window, the communities they're in, and a few examples. Pure."""
    now = now or timezone.now()
    since = (now - timedelta(days=WINDOW_DAYS)).timestamp()
    recent = [p for p in posts if float(p.get("created_utc") or 0) >= since]
    communities = Counter(p.get("subreddit", "") for p in recent if p.get("subreddit"))
    return {
        "mentions_90d": len(recent),
        "communities": [
            {"name": name, "mentions": count} for name, count in communities.most_common(TOP_COMMUNITIES)
        ],
        "community_count": len(communities),
        "sample": [
            {
                "title": p.get("title", "")[:200],
                "community": p.get("subreddit", ""),
                "url": f"https://www.reddit.com{p.get('permalink', '')}",
            }
            for p in recent[:SAMPLE_POSTS]
        ],
    }


def mentions(brand: str, client_id: str, client_secret: str) -> dict:
    """Posts mentioning the brand by exact name in the last 90 days."""
    token = _token(client_id, client_secret)
    params = {"q": f'"{brand}"', "sort": "new", "t": "year", "limit": 100, "type": "link"}
    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    try:
        reply = requests.get(SEARCH_URL, params=params, headers=headers, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise RedditError(f"Reddit couldn't be reached: {type(exc).__name__}") from exc
    if not reply.ok:
        raise RedditError(f"Reddit search answered HTTP {reply.status_code}")
    children = (reply.json().get("data") or {}).get("children") or []
    return summarize([c.get("data") or {} for c in children])
