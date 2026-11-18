"""The social presence score. Pure: no DB, no network.

Per platform, 0 to 100 points:
- presence 20: a profile exists (full points when the brand's own site links to it);
- audience 30: followers or subscribers, on a log scale (full at 100k);
- activity 30: posts in the last 30 days (full at 4);
- engagement 20: interactions per post, needs the brand's Buffer (step 2).

A part that can't be measured scores 0 and is listed under ``not_measured``, so
the score is never padded with guesses. Reddit has no brand profile to score;
it is scored on mentions: how often and in how many communities the brand
comes up in the last 90 days.

Overall: the average over all five platforms, so a platform the brand isn't on
counts against it.
"""

from __future__ import annotations

import math

PLATFORMS = ("linkedin", "x", "youtube", "instagram", "reddit")
LABELS = {
    "linkedin": "LinkedIn",
    "x": "X",
    "youtube": "YouTube",
    "instagram": "Instagram",
    "reddit": "Reddit",
}
PRESENCE, AUDIENCE, ACTIVITY, ENGAGEMENT = 20, 30, 30, 20
PRESENCE_OFF_SITE = 12  # found by search, not linked from the brand's site
AUDIENCE_FULL_AT = 100_000
ACTIVITY_FULL_AT = 4
MENTIONS_MAX, COMMUNITIES_MAX = 60, 40
COMMUNITY_POINTS = 8


def _audience(followers: int | None) -> float | None:
    if followers is None:
        return None
    return round(min(AUDIENCE, AUDIENCE * math.log10(followers + 1) / math.log10(AUDIENCE_FULL_AT + 1)), 1)


def _activity(posts_30d: int | None) -> float | None:
    if posts_30d is None:
        return None
    return round(min(ACTIVITY, ACTIVITY * posts_30d / ACTIVITY_FULL_AT), 1)


def profile_score(profile: dict) -> dict:
    """``profile``: url, on_site (linked from the brand's site), followers, posts_30d, engagement."""
    if not profile.get("url"):
        return {"score": 0.0, "parts": {"presence": 0.0}, "not_measured": [], "found": False}
    parts = {"presence": float(PRESENCE if profile.get("on_site") else PRESENCE_OFF_SITE)}
    not_measured = []
    measured = {
        "audience": _audience(profile.get("followers")),
        "activity": _activity(profile.get("posts_30d")),
        "engagement": profile.get("engagement"),
    }
    for part, points in measured.items():
        if points is None:
            not_measured.append(part)
        else:
            parts[part] = points
    return {
        "score": round(sum(parts.values()), 1),
        "parts": parts,
        "not_measured": not_measured,
        "found": True,
    }


def reddit_score(mentions: dict | None) -> dict:
    if mentions is None:
        return {"score": 0.0, "parts": {}, "not_measured": ["mentions"], "found": False}
    count, communities = mentions.get("mentions_90d", 0), mentions.get("community_count", 0)
    parts = {
        "mentions": round(min(MENTIONS_MAX, 15 * math.log2(count + 1)), 1),
        "communities": float(min(COMMUNITIES_MAX, COMMUNITY_POINTS * communities)),
    }
    return {"score": round(sum(parts.values()), 1), "parts": parts, "not_measured": [], "found": count > 0}


def overall(platform_scores: dict[str, dict]) -> dict:
    scores = [platform_scores.get(p, {}).get("score", 0.0) for p in PLATFORMS]
    partial = any(platform_scores.get(p, {}).get("not_measured") for p in PLATFORMS)
    return {"score": round(sum(scores) / len(PLATFORMS), 1), "partial": partial}
