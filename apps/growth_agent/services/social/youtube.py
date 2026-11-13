"""Public YouTube channel numbers from the YouTube Data API (needs ``YOUTUBE_API_KEY``).

Subscribers, videos and views are the channel's public statistics; activity is
the number of uploads in the last 30 days, read from its uploads playlist.
Docs: https://developers.google.com/youtube/v3/docs/channels/list
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlparse

import requests
from django.utils import timezone
from django.utils.dateparse import parse_datetime

API = "https://www.googleapis.com/youtube/v3"
TIMEOUT_SECONDS = 15
RECENT_DAYS = 30
RECENT_UPLOADS_CHECKED = 20


class YouTubeError(Exception):
    pass


def channel_query(profile_url: str) -> dict:
    """How to look the channel up: by @handle, channel id, or legacy username."""
    path = urlparse(profile_url or "").path.strip("/").split("/")
    if not path or not path[0]:
        return {}
    if path[0].startswith("@"):
        return {"forHandle": path[0]}
    if path[0] == "channel" and len(path) > 1:
        return {"id": path[1]}
    if path[0] == "user" and len(path) > 1:
        return {"forUsername": path[1]}
    if path[0] == "c" and len(path) > 1:
        return {"forHandle": f"@{path[1]}"}
    return {}


def _get(endpoint: str, params: dict) -> dict:
    try:
        reply = requests.get(f"{API}/{endpoint}", params=params, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise YouTubeError(f"YouTube couldn't be reached: {type(exc).__name__}") from exc
    if not reply.ok:
        raise YouTubeError(f"YouTube answered HTTP {reply.status_code}")
    return reply.json()


def _recent_uploads(uploads_playlist: str, api_key: str) -> tuple[int, str]:
    data = _get(
        "playlistItems",
        {
            "part": "contentDetails",
            "playlistId": uploads_playlist,
            "maxResults": RECENT_UPLOADS_CHECKED,
            "key": api_key,
        },
    )
    since = timezone.now() - timedelta(days=RECENT_DAYS)
    dates = [parse_datetime(i["contentDetails"].get("videoPublishedAt", "")) for i in data.get("items") or []]
    dates = [d for d in dates if d is not None]
    latest = max(dates).isoformat() if dates else ""
    return sum(1 for d in dates if d >= since), latest


def channel_stats(profile_url: str, api_key: str) -> dict | None:
    """Public numbers for the channel, or None when it can't be identified."""
    query = channel_query(profile_url)
    if not query:
        return None
    data = _get("channels", {"part": "statistics,contentDetails", "key": api_key, **query})
    items = data.get("items") or []
    if not items:
        return None
    channel = items[0]
    stats = channel.get("statistics") or {}
    uploads = ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads", "")
    recent, latest = _recent_uploads(uploads, api_key) if uploads else (0, "")
    return {
        "subscribers": None if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount", 0)),
        "videos": int(stats.get("videoCount", 0)),
        "views": int(stats.get("viewCount", 0)),
        "posts_30d": recent,
        "last_post": latest,
    }
