"""
Chat tools for what the agent measures beyond the plan: the brand vs its
competitors week by week (SEO / GEO / Brand mentions), keyword data, and site health.

Read tools return compact, plain-named data from stored results (no network).
The two "now" tools only queue a check, with the same once-a-day limit as the buttons.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from ...models import AgentActionLog, GrowthProgram
from ..keywords import refresh as keyword_refresh
from ..keywords.report import report as keyword_report
from ..rank_tracking import check as rank_check
from ..rank_tracking.report import report as tracking_report
from ..site_watch import watch

TOP_ROWS = 10
SITE_DAYS = 30


def _change(points: list[float]) -> float | None:
    return round(points[-1] - points[-2], 1) if len(points) > 1 else None


def _geo(built: dict) -> list[dict]:
    weeks = built["geo"]
    out = []
    for player in built["players"]:
        series = [w["brand"] if player["is_brand"] else w["rivals"].get(player["name"], 0.0) for w in weeks]
        out.append(
            {
                "brand": player["name"],
                "ai_answer_share_pct": series[-1] if series else None,
                "change": _change(series),
            }
        )
    return out


def _seo(built: dict) -> list[dict]:
    weeks = built["seo"]["weeks"]
    out = []
    for player in built["players"]:
        top = [w["domains"].get(player["domain"], {}).get("top10", 0) for w in weeks]
        latest = weeks[-1]["domains"].get(player["domain"], {}) if weeks else {}
        out.append(
            {
                "brand": player["name"],
                "keywords_in_google_top10": top[-1] if top else None,
                "of_tracked": weeks[-1]["keywords"] if weeks else None,
                "average_position_where_ranked": latest.get("average"),
                "change_in_top10": _change(top),
            }
        )
    return out


def _mentions(built: dict, names: dict) -> dict:
    """What the internet said about each player this week: counts, sentiment, and a few headlines."""
    weeks = built["brand"]["weeks"]
    latest = weeks[-1]["domains"] if weeks else {}
    return {
        "this_week": {names.get(d, d): counts for d, counts in latest.items()},
        "recent": {
            names.get(d, d): [{k: m[k] for k in ("title", "source", "sentiment", "url")} for m in rows[:3]]
            for d, rows in built["brand"]["latest"].items()
        },
    }


def get_competitor_tracking(program: GrowthProgram, _args: dict) -> dict:
    """The brand vs its top competitors, this week vs last: Google, AI answers, AI Overviews."""
    built = tracking_report(program)
    names = {p["domain"]: p["name"] for p in built["players"]}
    positions = [
        {
            "keyword": row["keyword"],
            "positions": {names.get(d, d): c["now"] for d, c in row["domains"].items()},
        }
        for row in built["seo"]["latest"]
    ]
    return {
        "players": [p["name"] for p in built["players"]],
        "geo_ai_answers": _geo(built),
        "seo_google": _seo(built),
        "google_positions_by_keyword": positions,
        "brand_mentions": _mentions(built, names),
        "last_check_at": built["last_check_at"],
        "where": "the You vs competitors column on the right (Tracking tab on small screens)",
        "note": "Positions: null means not in Google's top 100.",
    }


def get_keywords(program: GrowthProgram, _args: dict) -> dict:
    """Keywords the brand ranks for, keywords competitors win, and Search Console queries."""
    built = keyword_report(program)
    return {
        "brand_keywords": built["keywords"][:TOP_ROWS],
        "keyword_gaps": [
            {
                "keyword": g["keyword"],
                "monthly_searches": g["search_volume"],
                "who_ranks": g["rivals"][:2],
                "new": g.get("new"),
            }
            for g in built["gaps"][:TOP_ROWS]
        ],
        "search_console_queries": built["search_console"][:TOP_ROWS],
        "source": built["source"] or "none yet",
        "last_read_at": built["fetched_at"],
        "notes": built["notes"],
        "where": "the Keywords tab",
    }


def get_site_health(program: GrowthProgram, _args: dict) -> dict:
    """The latest daily site check and anything that changed on the site in the last 30 days."""
    last = (
        AgentActionLog.objects.filter(program=program, action=watch.CHECKED).order_by("-created_at").first()
    )
    since = timezone.now() - timedelta(days=SITE_DAYS)
    changes = AgentActionLog.objects.filter(
        program=program, action=watch.ANOMALY, created_at__gte=since
    ).order_by("-created_at")
    return {
        "last_check_at": last.created_at.isoformat() if last else None,
        "state": (last.detail or {}).get("state") if last else None,
        "changes_last_30_days": [
            {**(c.detail or {}), "at": c.created_at.isoformat()} for c in changes[:TOP_ROWS]
        ],
        "where": "Activity, filter 'Your site'",
        "technical_checks": _technical_checks(program),
    }


def _technical_checks(program: GrowthProgram) -> dict:
    """robots.txt AI crawlers and sitemap checks from the latest analysis (the Actions page's source)."""
    from apps.analyzer.services.technical_checks import latest_for_org  # noqa: PLC0415

    _, checks = latest_for_org(program.organization_id)
    return {
        "checked_at": checks[0].checked_at.isoformat() if checks else None,
        "checks": [{"check": c.label, "status": c.status, "detail": c.detail} for c in checks],
        "where": "Failed checks are On-site actions on the Actions page; '✓ Passed' lists the healthy ones.",
    }


def check_ranks_now(program: GrowthProgram, _args: dict) -> dict:
    if rank_check.start(program, guard=rank_check.START_GUARD):
        return {
            "started": True,
            "note": "Checking Google positions and AI Overviews now; results in a few minutes.",
        }
    return {
        "started": False,
        "reason": "Positions were checked in the last day; they can be checked once a day.",
    }


def refresh_keywords_now(program: GrowthProgram, _args: dict) -> dict:
    if keyword_refresh.start(program, guard=keyword_refresh.START_GUARD):
        return {"started": True, "note": "Reading keywords now; about a minute."}
    return {"started": False, "reason": "Keywords were read in the last day; they refresh once a day."}


def get_prompt_presence(program: GrowthProgram, _args: dict) -> dict:
    """Where the brand's prompts are discussed and whether the brand shows up there."""
    from ..prompt_presence import check as prompt_presence  # noqa: PLC0415

    data = prompt_presence.report(program)
    return {
        "checked_at": data["checked_at"],
        "prompts": [
            {
                "prompt": p["prompt"],
                "brand_present_on": p["brand_present_on"],
                "top_subreddits": [s["name"] for s in p["subreddits"][:3]],
                "reddit_threads": [r["title"] for r in p["reddit"][:3]],
                "linkedin_posts": [r["title"] for r in p["linkedin"][:3]],
                "x_posts": [r["title"] for r in p["x"][:3]],
                "google_top": [r["url"] for r in p["google"][:3]],
            }
            for p in data["prompts"][:TOP_ROWS]
        ],
        "where": "the Prompts tab",
    }
