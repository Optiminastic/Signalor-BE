"""
Read-only chat tools. Each returns a compact dict built from the brand's real
data, trimmed so the model's context stays small. Everything is scoped to the
program passed in; nothing here takes an id from the model except search text.
"""

from __future__ import annotations

from apps.organizations.services.retrieval import build_knowledge_block

from ...models import GrowthProgram
from ..indexing import bing, satellite_rank
from ..indexing import report as indexing_report
from ..planning.plan import active_cycle
from ..positioning.report import latest_report
from ..social import check as social_check

MAX_GAPS = 8
MAX_LOST = 5
MAX_ACTIVITY = 15
KNOWLEDGE_CHARS = 1500


def get_positioning(program: GrowthProgram, _args: dict) -> dict:
    report = latest_report(program)
    if report is None or report.status != "complete":
        return {"available": False, "note": "No positioning report yet."}
    context = report.context_gaps or {}
    engines = (report.share_of_voice or {}).get("engines") or {}
    return {
        "headline": report.headline,
        "brand_visibility_by_engine_pct": {e: r.get("brand", 0) for e, r in engines.items()},
        "citation_gaps": [
            {k: g.get(k) for k in ("domain", "source_type", "prompts", "engines", "rivals_present")}
            for g in report.citation_gaps[:MAX_GAPS]
        ],
        "lost_prompts": report.lost_prompts[:MAX_LOST],
        "brands_ai_recommends_instead": context.get("ai_recommended_brands", []),
        "missing_topics": context.get("missing_topics", []),
        "why_ai_prefers_others": context.get("summary", ""),
        "entity_gaps": [g.get("label") for g in report.entity_gaps],
    }


def _weekly_routines(program: GrowthProgram) -> list[dict]:
    """Work the agent repeats every week besides the dated tasks, with whether each is running."""
    indexing = indexing_report.build(program.organization)["status"]
    return [
        {
            "routine": "Google indexing check (Search Console)",
            "running": indexing not in ("not_connected", "no_property"),
        },
        {"routine": "Send new and changed pages to Bing", "running": bing.enabled(program)},
        {
            "routine": "Google ranking check of articles on signalor.ai sites",
            "running": satellite_rank.ops_search_console() is not None,
        },
        {"routine": "Weekly summary in this chat", "running": program.status == GrowthProgram.Status.ACTIVE},
    ]


def get_plan(program: GrowthProgram, _args: dict) -> dict:
    cycle = active_cycle(program)
    if cycle is None:
        return {"available": False, "note": "No active month plan."}
    return {
        "program_status": program.status,
        "starts_on": cycle.starts_on.isoformat(),
        "ends_on": cycle.ends_on.isoformat(),
        "baseline_visibility_pct": cycle.baseline_share_of_voice,
        "target_visibility_pct": cycle.target_share_of_voice,
        "weekly_routines": _weekly_routines(program),
        "tasks": [
            {
                "id": t.id,
                "week": t.week,
                "date": t.scheduled_for.isoformat(),
                "channel": t.channel,
                "tactic": t.tactic,
                "title": t.title,
                # For satellite articles: the signalor.ai site the article goes on.
                "site": t.target_url if t.tactic == "satellite_article" else "",
                "status": t.status,
                "impact": t.impact_score,
                "waiting_for": (t.result_payload or {}).get("waiting_for", ""),
            }
            for t in cycle.tasks.all()
        ],
    }


def get_progress(program: GrowthProgram, _args: dict) -> dict:
    cycle = active_cycle(program)
    if cycle is None:
        return {"available": False, "note": "No active month plan."}
    return {
        "baseline_visibility_pct": cycle.baseline_share_of_voice,
        "target_visibility_pct": cycle.target_share_of_voice,
        "snapshots": [
            {
                "week": s.week,
                "kind": s.kind,
                "status": s.status,
                "visibility_pct": s.share_of_voice,
                "delta_vs_baseline": s.delta_vs_baseline,
                "by_engine_pct": s.engines,
                "sites_now_citing_brand": s.closed_gaps,
            }
            for s in cycle.snapshots.order_by("started_at")
        ],
    }


def get_activity(program: GrowthProgram, _args: dict) -> dict:
    logs = program.action_logs.select_related("task").order_by("-created_at")[:MAX_ACTIVITY]
    return {
        "recent_actions": [
            {
                "when": log.created_at.isoformat(),
                "action": log.action,
                "outcome": log.outcome,
                "task": log.task.title if log.task else "",
                "url": (log.detail or {}).get("url", ""),
                "reason": (log.detail or {}).get("reason") or (log.detail or {}).get("error", ""),
            }
            for log in logs
        ]
    }


def search_brand_knowledge(program: GrowthProgram, args: dict) -> dict:
    query = str(args.get("query", "")).strip()[:300]
    if not query:
        return {"error": "query is required"}
    text = build_knowledge_block(program.organization, query, max_chars=KNOWLEDGE_CHARS)
    return {"results": text or "Nothing relevant found in the brand's site content."}


def _indexing_summary(program: GrowthProgram) -> dict:
    built = indexing_report.build(program.organization)
    return {
        "search_console": built["status"],
        "pages_checked": built["checked"],
        "pages_on_google": built["indexed"],
        "change_since_last_check": built["indexed_change"],
        "last_checked": built["checked_at"].isoformat() if built["checked_at"] else None,
        "top_reasons_not_indexed": [
            {"reason": r["label"], "pages": r["count"]} for r in built["reasons"][:3]
        ],
        "pages_ready_to_request_in_search_console": len(built["to_request"]),
    }


def _satellite_summary(program: GrowthProgram) -> dict:
    cycle = active_cycle(program)
    planned = cycle.tasks.filter(tactic="satellite_article").order_by("scheduled_for") if cycle else []
    ranks = satellite_rank.articles(program)
    return {
        "planned_topics": [
            {"date": t.scheduled_for.isoformat(), "site": t.target_url, "topic": t.target, "status": t.status}
            for t in planned
        ],
        "rankings_tracked": ranks["search_console"],
        "published": [
            {
                "topic": r["topic"],
                "url": r["url"],
                "on_google": (r["check"] or {}).get("indexed"),
                "position": (r["check"] or {}).get("position"),
                "clicks": (r["check"] or {}).get("clicks"),
                "needs_attention": r["flag"],
            }
            for r in ranks["articles"]
        ],
    }


def get_indexing(program: GrowthProgram, _args: dict) -> dict:
    """Google indexing, weekly Bing sending, and articles on signalor.ai's satellite sites."""
    state = bing.state(program)
    return {
        "google": _indexing_summary(program),
        "bing_weekly_sending": {"enabled": state["enabled"], "last_run": state["last"]},
        "satellite_articles": _satellite_summary(program),
    }


def get_social(program: GrowthProgram, _args: dict) -> dict:
    """The latest social presence check, without the raw profile details."""
    latest = social_check.report(program)
    if latest.get("status") in ("never_checked", "checking") and not latest.get("checked_at"):
        return {"available": False, "note": "The first social check hasn't run yet."}
    return {
        "overall_score": latest["overall"]["score"],
        "partial": latest["overall"]["partial"],
        "change_since_last_week": latest["change"],
        "platforms": {
            name: {"score": data["score"], "found": data["found"], "not_measured": data["not_measured"]}
            for name, data in latest["platforms"].items()
        },
        "notes": latest["notes"],
    }
