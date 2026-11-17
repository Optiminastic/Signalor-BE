"""
Weekly rank check for a brand and its top competitors.

- SEO: one Serper Google search (top 100) per tracked keyword; each domain's position.
- Brand: what the internet said about each player this past week (Google web,
  news and Reddit), see ``brand_mentions``.

The week's rows replace any earlier rows for the same week, so a re-run never
double counts. Runs on the worker, at most once a day on request, weekly on schedule.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.llm import serper

from ...models import AgentActionLog, GrowthProgram, KeywordSnapshot, RankObservation, TrackedKeyword
from ..brand_mentions import check as mentions
from ..execution import audit
from ..execution.brand import display_name
from ..keywords import gaps as gap_rules
from ..keywords.accounts import search_console_queries
from ..keywords.refresh import latest_run
from ..positioning.inputs import bare_domain
from . import rules, suggest

logger = logging.getLogger("apps")

STARTED = "rank_check_started"
DONE = "rank_check_done"
START_GUARD = timedelta(hours=24)
CHECK_EVERY = timedelta(days=6)
SERP_DEPTH = 100


def competitors(program: GrowthProgram) -> list[gap_rules.Rival]:
    """The brand first, then its top 3 competitors (the same ones as keyword gaps)."""
    org = program.organization
    domain = bare_domain(org.url)
    brand = gap_rules.Rival(display_name(org), domain)
    run, report = latest_run(program)
    if run is None:
        return [brand] if domain else []
    candidates = [gap_rules.Rival(c.name, bare_domain(c.url)) for c in run.competitors.all().order_by("id")]
    return [brand, *gap_rules.top_rivals(candidates, report.lost_prompts, own_domain=domain)]


def fill_keywords(program: GrowthProgram) -> int:
    """Top up the tracked list to 15 with the agent's picks. Returns how many were added."""
    known = set(program.tracked_keywords.values_list("keyword", flat=True))
    room = rules.MAX_KEYWORDS - program.tracked_keywords.filter(active=True).count()
    if room <= 0:
        return 0
    brand = program.keyword_snapshots.filter(kind=KeywordSnapshot.Kind.BRAND).first()
    gaps = program.keyword_snapshots.filter(kind=KeywordSnapshot.Kind.GAPS).first()
    _, report = latest_run(program)
    prompts = [p.get("prompt", "") for p in (report.lost_prompts if report else [])]
    queries = [{"keyword": q.get("query", "")} for q in search_console_queries(program.organization)]
    picked = rules.pick_keywords(
        brand_rows=(brand.rows if brand else []) + queries,
        gap_rows=gaps.rows if gaps else [],
        prompts=prompts,
        taken=known,
        room=room,
    )
    if len(picked) < room and report is not None:
        # No keyword data yet: turn the brand's topics and AI prompts into short Google keywords.
        topics = (report.context_gaps or {}).get("missing_topics") or []
        ideas = suggest.suggest(program, topics, prompts, room - len(picked))
        picked += [k for k in ideas if k not in known and k not in picked]
    TrackedKeyword.objects.bulk_create([TrackedKeyword(program=program, keyword=k) for k in picked[:room]])
    return min(len(picked), room)


def _seo_rows(keywords: list[str], domains: list[str]) -> list[dict]:
    rows = []
    for keyword in keywords:
        data = serper.search(keyword, num=SERP_DEPTH)
        if data is None:
            continue  # unknown, not "not ranked": no rows rather than wrong ones
        for domain, position in rules.positions(data.get("organic") or [], domains).items():
            rows.append({"keyword": keyword, "domain": domain, "position": position})
    return rows


def _store(program: GrowthProgram, week, kind: str, rows: list[dict]) -> None:
    RankObservation.objects.filter(program=program, week=week, kind=kind).delete()
    RankObservation.objects.bulk_create(
        [RankObservation(program=program, week=week, kind=kind, **row) for row in rows]
    )


def run_check(program: GrowthProgram) -> dict:
    """Check this week's Google positions and brand mentions. Worker only."""
    fill_keywords(program)
    players = [p for p in competitors(program) if p.domain]
    domains = [p.domain for p in players]
    keywords = list(program.tracked_keywords.filter(active=True).values_list("keyword", flat=True))
    week = rules.week_of(timezone.localdate())
    seo = _seo_rows(keywords, domains) if domains else []
    found = mentions.run(program, players, week)
    with transaction.atomic():
        if seo:
            _store(program, week, RankObservation.Kind.SEO, seo)
        detail = {"keywords": len(keywords), "domains": domains, "seo_rows": len(seo), "mentions": found}
        audit.record(program, None, DONE, AgentActionLog.Outcome.SUCCEEDED, detail)
    return detail


def start(program: GrowthProgram, *, guard: timedelta, actor_email: str = "") -> bool:
    """Queue a check, at most once per ``guard`` (each one costs search calls)."""
    from ...analysis_tasks import dispatch_rank_check

    last = AgentActionLog.objects.filter(program=program, action=STARTED).order_by("-created_at").first()
    if last is not None and timezone.now() - last.created_at < guard:
        return False
    audit.record(program, None, STARTED, AgentActionLog.Outcome.SUCCEEDED, {}, actor_email=actor_email)
    transaction.on_commit(lambda: dispatch_rank_check(program.id))
    return True


def weekly(program: GrowthProgram) -> bool:
    return start(program, guard=CHECK_EVERY)
