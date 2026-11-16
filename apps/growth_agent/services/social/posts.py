"""Plan next week's social posts: one per platform the brand is on.

Runs after the weekly social check. Each post is a task (tactic ``social_post``,
channel ``owned_social``) in the active plan; the agent drafts it on its date
and the owner edits, approves or rejects it. Posted platforms: LinkedIn, X and
Instagram. YouTube needs video and Reddit is never posted to automatically.

Topics rotate through what the brand already has: its articles on signalor.ai
sites, then the topics AI answers cover without it.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlparse

from django.utils import timezone

from ...models import GrowthProgram, GrowthTask
from ..planning.plan import active_cycle
from . import check

POSTED_PLATFORMS = {"linkedin": "LinkedIn", "x": "X", "instagram": "Instagram"}
TACTIC = "social_post"
CHANNEL = "owned_social"
HOSTS = {"linkedin.com": "linkedin", "x.com": "x", "twitter.com": "x", "instagram.com": "instagram"}


def platform_of(profile_url: str) -> str:
    host = (urlparse(profile_url or "").netloc or "").lower().removeprefix("www.")
    return HOSTS.get(host, "")


def _profiles(program: GrowthProgram) -> dict[str, str]:
    """Platform -> profile URL, from the latest social check."""
    latest = check.report(program)
    platforms = latest.get("platforms") or {}
    return {
        name: (platforms.get(name, {}).get("profile") or {}).get("url", "")
        for name in POSTED_PLATFORMS
        if (platforms.get(name, {}).get("profile") or {}).get("url")
    }


def _topics(program: GrowthProgram, cycle) -> list[str]:
    articles = (
        cycle.tasks.filter(tactic="satellite_article")
        .order_by("scheduled_for")
        .values_list("target", flat=True)
    )
    report = cycle.positioning_report
    missing = ((report.context_gaps or {}).get("missing_topics") or []) if report else []
    seen, ordered = set(), []
    for topic in [*articles, *missing]:
        if topic and topic.lower() not in seen:
            seen.add(topic.lower())
            ordered.append(topic)
    return ordered


def _already_planned(cycle, start, end) -> set[str]:
    planned = cycle.tasks.filter(tactic=TACTIC, scheduled_for__gte=start, scheduled_for__lt=end)
    return {platform_of(url) for url in planned.values_list("target_url", flat=True)}


def queue_weekly(program: GrowthProgram) -> int:
    """Next week's post tasks, one per platform with a profile, never twice for a week."""
    cycle = active_cycle(program)
    if cycle is None:
        return 0
    start = timezone.now().date() + timedelta(days=1)
    end = start + timedelta(days=7)
    profiles, topics = _profiles(program), _topics(program, cycle)
    done = _already_planned(cycle, start, end)
    used = cycle.tasks.filter(tactic=TACTIC).count()
    created = 0
    for offset, (platform, url) in enumerate(sorted(profiles.items())):
        if platform in done or not topics or start + timedelta(days=offset * 2) > cycle.ends_on:
            continue
        topic = topics[(used + created) % len(topics)]
        day = start + timedelta(days=offset * 2)
        GrowthTask.objects.create(
            cycle=cycle,
            week=min(4, max(1, (day - cycle.starts_on).days // 7 + 1)),
            scheduled_for=day,
            channel=CHANNEL,
            tactic=TACTIC,
            title=f"Post on {POSTED_PLATFORMS[platform]}: {topic}"[:300],
            rationale="One post a week per platform keeps the brand active where its audience is.",
            target=topic,
            target_url=url,
            prompts=[topic],
        )
        created += 1
    return created
