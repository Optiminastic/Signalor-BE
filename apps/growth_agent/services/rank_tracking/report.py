"""
The tracking panel (SEO / GEO / Brand): the brand and its competitors, week by
week, for the last 8 weeks. No network.

GEO comes from the weekly AI-visibility snapshots the agent already takes; SEO
from the weekly rank check; Brand from the weekly brand-mention search.
"""

from __future__ import annotations

from collections import defaultdict

from ...models import AgentActionLog, GrowthProgram, PositioningReport, RankObservation, WeeklySnapshot
from ..brand_mentions import report as mentions_report
from . import rules
from .check import DONE, STARTED, competitors


def _baseline(program: GrowthProgram) -> dict | None:
    """The first positioning report's share of voice: GEO's starting point before any weekly snapshot."""
    first = (
        program.positioning_reports.filter(status=PositioningReport.Status.COMPLETE)
        .exclude(share_of_voice={})
        .order_by("created_at")
        .first()
    )
    overall = (first.share_of_voice or {}).get("overall") if first else None
    if not overall:
        return None
    return {
        "week": rules.week_of(first.created_at.date()).isoformat(),
        "brand": overall.get("brand", 0.0),
        "rivals": overall.get("rivals", {}),
        "engines": {},
    }


def _geo(program: GrowthProgram) -> list[dict]:
    snapshots = WeeklySnapshot.objects.filter(
        cycle__program=program, status=WeeklySnapshot.Status.COMPLETE
    ).order_by("-completed_at")[: rules.WEEKS_SHOWN]
    weeks = [
        {
            "week": rules.week_of(s.completed_at.date()).isoformat(),
            "brand": s.share_of_voice,
            "rivals": s.rivals or {},
            "engines": s.engines or {},
        }
        for s in reversed(list(snapshots))
    ]
    start = _baseline(program)
    if start and (not weeks or start["week"] < weeks[0]["week"]):
        weeks = [start, *weeks][-rules.WEEKS_SHOWN :]
    return weeks


def _weeks(program: GrowthProgram) -> dict:
    rows = list(
        RankObservation.objects.filter(program=program, kind=RankObservation.Kind.SEO)
        .order_by("-week")
        .values("week", "keyword", "domain", "position")
    )
    by_week: dict = defaultdict(list)
    for row in rows:
        by_week[row["week"]].append(row)
    return dict(sorted(by_week.items())[-rules.WEEKS_SHOWN :])


def _table(latest: list[dict], previous: list[dict]) -> list[dict]:
    """Latest position per keyword and domain, with last week's for comparison."""
    before = {(r["keyword"], r["domain"]): r["position"] for r in previous}
    by_keyword: dict = defaultdict(dict)
    for row in latest:
        by_keyword[row["keyword"]][row["domain"]] = {
            "now": row["position"],
            "before": before.get((row["keyword"], row["domain"])),
        }
    return [{"keyword": k, "domains": v} for k, v in sorted(by_keyword.items())]


def _seo(program: GrowthProgram, domains: list[str]) -> dict:
    weeks = _weeks(program)
    dates = list(weeks)
    latest = weeks[dates[-1]] if dates else []
    previous = weeks[dates[-2]] if len(dates) > 1 else []
    return {
        "weeks": [
            {
                "week": d.isoformat(),
                "keywords": len({r["keyword"] for r in weeks[d]}),
                "domains": rules.seo_week_summary(weeks[d], domains),
            }
            for d in dates
        ],
        "latest": _table(latest, previous),
    }


def _state(program: GrowthProgram) -> dict:
    done = AgentActionLog.objects.filter(program=program, action=DONE).order_by("-created_at").first()
    started = AgentActionLog.objects.filter(program=program, action=STARTED).order_by("-created_at").first()
    checking = started is not None and (done is None or started.created_at > done.created_at)
    return {"checking": checking, "last_check_at": done.created_at.isoformat() if done else None}


def report(program: GrowthProgram) -> dict:
    players = competitors(program)
    domains = [p.domain for p in players if p.domain]
    keywords = program.tracked_keywords.filter(active=True)
    return {
        "players": [{"name": p.name, "domain": p.domain, "is_brand": i == 0} for i, p in enumerate(players)],
        "keywords": [{"id": k.id, "keyword": k.keyword, "source": k.source} for k in keywords],
        "geo": _geo(program),
        "seo": _seo(program, domains),
        "brand": mentions_report.section(program, domains),
        **_state(program),
    }
