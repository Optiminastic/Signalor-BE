"""The weekly social check for one brand, and the report the Social tab shows.

Inputs, each used only when available:
- profiles found by the latest site analysis (``BrandVisibility.social_presence_details``);
- YouTube's public channel numbers (``YOUTUBE_API_KEY``);
- Reddit mentions in the last 90 days (``REDDIT_CLIENT_ID`` / ``REDDIT_CLIENT_SECRET``).

Each check is kept in the Agent log (``social_checked``) with its full result,
so the tab can show this week's score and the change since last week without
a new table. Network calls run on the worker queue, never in a request.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.analyzer.models import AnalysisRun

from ...models import AgentActionLog, GrowthProgram
from ..execution import audit
from ..execution.brand import display_name
from . import reddit, score, youtube

logger = logging.getLogger("apps")
CHECKED, STARTED = "social_checked", "social_check_started"
CHECK_EVERY = timedelta(days=7)
START_GUARD = timedelta(hours=6)
# The site analysis calls X "twitter"; the tab calls it X.
STORED_NAMES = {"linkedin": "linkedin", "x": "twitter", "youtube": "youtube", "instagram": "instagram"}


def _stored_profiles(program: GrowthProgram) -> dict:
    run = (
        AnalysisRun.objects.filter(organization=program.organization, status="complete")
        .select_related("brand_visibility")
        .order_by("-created_at")
        .first()
    )
    visibility = getattr(run, "brand_visibility", None) if run else None
    return (visibility.social_presence_details if visibility else None) or {}


def _profile(stored: dict) -> dict:
    return {
        "url": stored.get("url") or "",
        "on_site": stored.get("source") == "website",
        "followers": stored.get("followers"),
        "posts_30d": None,
        "engagement": None,
        "source": stored.get("source", "none"),
    }


def _with_youtube(profile: dict, notes: list[str]) -> dict:
    key = getattr(settings, "YOUTUBE_API_KEY", "")
    if not profile["url"] or not key:
        if profile["url"] and not key:
            notes.append("YouTube numbers need a YouTube Data API key on the server.")
        return profile
    try:
        stats = youtube.channel_stats(profile["url"], key)
    except youtube.YouTubeError as exc:
        notes.append(f"YouTube couldn't be read this week: {exc}")
        return profile
    if not stats:
        return profile
    return {**profile, "followers": stats["subscribers"], "posts_30d": stats["posts_30d"], "youtube": stats}


def _reddit(program: GrowthProgram, notes: list[str]) -> dict | None:
    client_id, secret = (
        getattr(settings, "REDDIT_CLIENT_ID", ""),
        getattr(settings, "REDDIT_CLIENT_SECRET", ""),
    )
    if not client_id or not secret:
        notes.append("Reddit mentions need a Reddit API app on the server.")
        return None
    try:
        return reddit.mentions(display_name(program.organization), client_id, secret)
    except reddit.RedditError as exc:
        notes.append(f"Reddit couldn't be read this week: {exc}")
        return None


def measure(program: GrowthProgram) -> dict:
    """This week's numbers and scores. Network: YouTube and Reddit, when their keys are set."""
    stored, notes = _stored_profiles(program), []
    profiles = {name: _profile(stored.get(key) or {}) for name, key in STORED_NAMES.items()}
    profiles["youtube"] = _with_youtube(profiles["youtube"], notes)
    mentions = _reddit(program, notes)
    scores = {name: score.profile_score(profile) for name, profile in profiles.items()}
    scores["reddit"] = score.reddit_score(mentions)
    if not stored:
        notes.append("No site analysis has found the brand's social profiles yet.")
    return {
        "overall": score.overall(scores),
        "platforms": {name: {**scores[name], "profile": profiles.get(name)} for name in score.PLATFORMS},
        "reddit": mentions,
        "notes": notes,
    }


def run_check(program: GrowthProgram) -> None:
    """Measure and keep the result. Runs on the worker."""
    audit.record(program, None, CHECKED, AgentActionLog.Outcome.SUCCEEDED, measure(program))


def start(program: GrowthProgram, *, guard: timedelta, actor_email: str = "") -> bool:
    from ...analysis_tasks import dispatch_social_check

    last = AgentActionLog.objects.filter(program=program, action=STARTED).order_by("-created_at").first()
    if last is not None and timezone.now() - last.created_at < guard:
        return False
    audit.record(program, None, STARTED, AgentActionLog.Outcome.SUCCEEDED, {}, actor_email=actor_email)
    transaction.on_commit(lambda: dispatch_social_check(program.id))
    return True


def weekly(program: GrowthProgram) -> bool:
    return start(program, guard=CHECK_EVERY)


def report(program: GrowthProgram) -> dict:
    """The latest check and the change since the one before. No network."""
    checks = list(AgentActionLog.objects.filter(program=program, action=CHECKED).order_by("-created_at")[:2])
    started = AgentActionLog.objects.filter(program=program, action=STARTED).order_by("-created_at").first()
    checking = started is not None and (not checks or started.created_at > checks[0].created_at)
    if not checks:
        return {"status": "checking" if checking else "never_checked", "checked_at": None}
    latest = checks[0].detail
    previous = checks[1].detail if len(checks) > 1 else None
    change = None if previous is None else round(latest["overall"]["score"] - previous["overall"]["score"], 1)
    return {
        "status": "checking" if checking else "ready",
        "checked_at": checks[0].created_at,
        "change": change,
        "labels": score.LABELS,
        **latest,
    }
