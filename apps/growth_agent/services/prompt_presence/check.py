"""
Prompt presence: for each prompt the brand tracks, where it's being discussed
(Google, Reddit, LinkedIn, X) and whether the brand already shows up there.

Weekly, and on request at most hourly. Each prompt costs 4 Google searches, so
at most ``MAX_PROMPTS`` prompts are checked. The week's rows replace any earlier
rows for the same week, so a re-run never double counts.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.analyzer.models import PromptTrack

from ...models import AgentActionLog, GrowthProgram, PromptPresence
from ..execution import audit
from ..execution.brand import display_name
from ..positioning.inputs import bare_domain
from ..rank_tracking.rules import week_of
from . import search

logger = logging.getLogger("apps")

STARTED, CHECKED = "prompt_presence_started", "prompt_presence_checked"
CHECK_EVERY = timedelta(days=7)
START_GUARD = timedelta(hours=1)
MAX_PROMPTS = 15
PLATFORMS = [p.value for p in PromptPresence.Platform]


def tracked_prompts(program: GrowthProgram) -> list[str]:
    """The brand's tracked prompts from its newest analysis that has any (they carry over between runs)."""
    track = (
        PromptTrack.objects.filter(analysis_run__organization=program.organization, deleted_at__isnull=True)
        .select_related("analysis_run")
        .order_by("-analysis_run__created_at", "id")
        .first()
    )
    if track is None:
        return []
    texts = (
        PromptTrack.objects.filter(analysis_run=track.analysis_run, deleted_at__isnull=True)
        .order_by("id")
        .values_list("prompt_text", flat=True)
    )
    return list(dict.fromkeys(t.strip() for t in texts if t and t.strip()))[:MAX_PROMPTS]


def run_check(program: GrowthProgram) -> dict:
    """Search every tracked prompt on every platform and keep this week's results. Worker only."""
    org = program.organization
    name, domain = display_name(org), bare_domain(org.url or "")
    week = week_of(timezone.localdate())
    rows: list[PromptPresence] = []
    prompts = tracked_prompts(program)
    for prompt in prompts:
        found = search.search_prompt(prompt)
        if found is None:
            break  # search not configured: nothing to store
        for results in found.values():
            rows += [
                PromptPresence(
                    program=program,
                    week=week,
                    prompt=prompt[:500],
                    platform=r.platform,
                    url=r.url[:1000],
                    title=r.title,
                    snippet=r.snippet,
                    community=r.community,
                    position=r.position,
                    mentions_brand=search.mentions_brand(r, name=name, domain=domain),
                )
                for r in results
            ]
    with transaction.atomic():
        PromptPresence.objects.filter(program=program, week=week).delete()
        PromptPresence.objects.bulk_create(rows, ignore_conflicts=True)
        detail = {
            "prompts": len(prompts),
            "results": len(rows),
            "with_brand": sum(r.mentions_brand for r in rows),
        }
        audit.record(program, None, CHECKED, AgentActionLog.Outcome.SUCCEEDED, detail)
    return detail


def start(program: GrowthProgram, *, guard: timedelta, actor_email: str = "") -> bool:
    from ...analysis_tasks import dispatch_prompt_presence  # noqa: PLC0415  (avoids an import cycle)

    last = AgentActionLog.objects.filter(program=program, action=STARTED).order_by("-created_at").first()
    if last is not None and timezone.now() - last.created_at < guard:
        return False
    audit.record(program, None, STARTED, AgentActionLog.Outcome.SUCCEEDED, {}, actor_email=actor_email)
    transaction.on_commit(lambda: dispatch_prompt_presence(program.id))
    return True


def weekly(program: GrowthProgram) -> bool:
    return start(program, guard=CHECK_EVERY)


def checking(program: GrowthProgram) -> bool:
    started = AgentActionLog.objects.filter(program=program, action=STARTED).order_by("-created_at").first()
    done = AgentActionLog.objects.filter(program=program, action=CHECKED).order_by("-created_at").first()
    return started is not None and (done is None or started.created_at > done.created_at)


def _row(r: PromptPresence) -> dict:
    return {
        "url": r.url,
        "title": r.title,
        "snippet": r.snippet,
        "community": r.community,
        "position": r.position,
        "mentions_brand": r.mentions_brand,
    }


def _subreddits(rows: list[PromptPresence]) -> list[dict]:
    counts: dict[str, int] = {}
    for r in rows:
        if r.community:
            counts[r.community] = counts.get(r.community, 0) + 1
    return [{"name": k, "threads": v} for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def report(program: GrowthProgram) -> dict:
    """What the Prompts tab shows: per prompt, the posts per platform and where the brand appears. No network."""
    latest = (
        PromptPresence.objects.filter(program=program)
        .order_by("-week")
        .values_list("week", flat=True)
        .first()
    )
    last_check = (
        AgentActionLog.objects.filter(program=program, action=CHECKED).order_by("-created_at").first()
    )
    rows = list(PromptPresence.objects.filter(program=program, week=latest)) if latest else []
    by_prompt: dict[str, list[PromptPresence]] = {}
    for r in rows:
        by_prompt.setdefault(r.prompt, []).append(r)
    prompts = [
        {
            "prompt": prompt,
            **{p: [_row(r) for r in found if r.platform == p] for p in PLATFORMS},
            "subreddits": _subreddits([r for r in found if r.platform == "reddit"]),
            "brand_present_on": sorted({r.platform for r in found if r.mentions_brand}),
        }
        for prompt, found in by_prompt.items()
    ]
    return {
        "checked_at": last_check.created_at.isoformat() if last_check else None,
        "checking": checking(program),
        "tracked_prompts": len(tracked_prompts(program)),
        "prompts": prompts,
    }
