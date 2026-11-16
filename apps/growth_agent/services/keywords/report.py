"""The Keywords tab: the brand's keywords and its gaps against competitors. No network."""

from __future__ import annotations

from django.conf import settings

from ...models import AgentActionLog, GrowthProgram, KeywordSnapshot
from .refresh import DONE, STARTED

NOTES = {
    "no_analysis": "Keywords need a finished site analysis with competitors. Run one from the dashboard first.",
    "not_configured": "Keyword data isn't set up on the server yet.",
    "provider_error": "Keyword data is temporarily unavailable. The agent will try again next week.",
}


def _latest(program: GrowthProgram, kind: str) -> KeywordSnapshot | None:
    return program.keyword_snapshots.filter(kind=kind).first()


def _state(program: GrowthProgram) -> dict:
    done = AgentActionLog.objects.filter(program=program, action=DONE).order_by("-created_at").first()
    started = AgentActionLog.objects.filter(program=program, action=STARTED).order_by("-created_at").first()
    refreshing = started is not None and (done is None or started.created_at > done.created_at)
    failed = done is not None and done.outcome == AgentActionLog.Outcome.FAILED
    reason = (done.detail or {}).get("reason", "") if failed else ""
    return {"refreshing": refreshing, "note": NOTES.get(reason, "")}


def report(program: GrowthProgram) -> dict:
    brand = _latest(program, KeywordSnapshot.Kind.BRAND)
    gaps = _latest(program, KeywordSnapshot.Kind.GAPS)
    state = _state(program)
    notes = [state["note"]] if state["note"] else []
    if not getattr(settings, "DATAFORSEO_LOGIN", ""):
        notes = [NOTES["not_configured"]]
    gap_rows = gaps.rows if gaps else []
    return {
        "keywords": brand.rows if brand else [],
        "gaps": gap_rows,
        "new_gaps": sum(1 for g in gap_rows if g.get("new")),
        "competitors": (gaps.meta or {}).get("competitors", []) if gaps else [],
        "fetched_at": gaps.fetched_at.isoformat() if gaps else None,
        "refreshing": state["refreshing"],
        "notes": notes,
    }
