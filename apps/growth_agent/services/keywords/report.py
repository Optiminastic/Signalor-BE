"""The Keywords tab: the brand's keywords and its gaps against competitors. No network."""

from __future__ import annotations

from django.conf import settings

from ...models import AgentActionLog, GrowthProgram, KeywordSnapshot
from .. import run_guard
from .accounts import own_source, search_console_queries
from .refresh import DONE, STARTED

NOTES = {
    "no_analysis": "Keywords need a finished site analysis with competitors. Run one from the dashboard first.",
    "not_configured": "Keyword data isn't set up on the server yet.",
    "own_account_error": "Your Semrush or Ahrefs account refused the last read (key or units). Check it in the + panel.",
    "provider_error": "Keyword data is temporarily unavailable. The agent will try again next week.",
}


def _latest(program: GrowthProgram, kind: str) -> KeywordSnapshot | None:
    return program.keyword_snapshots.filter(kind=kind).first()


def _state(program: GrowthProgram) -> dict:
    done = AgentActionLog.objects.filter(program=program, action=DONE).order_by("-created_at").first()
    refreshing = run_guard.is_running(program, STARTED, DONE)
    failed = done is not None and done.outcome == AgentActionLog.Outcome.FAILED
    reason = (done.detail or {}).get("reason", "") if failed else ""
    return {"refreshing": refreshing, "note": NOTES.get(reason, "")}


def report(program: GrowthProgram) -> dict:
    brand = _latest(program, KeywordSnapshot.Kind.BRAND)
    gaps = _latest(program, KeywordSnapshot.Kind.GAPS)
    state = _state(program)
    notes = [state["note"]] if state["note"] else []
    if not getattr(settings, "DATAFORSEO_LOGIN", "") and own_source(program.organization) is None:
        notes = [NOTES["not_configured"]]
    gap_rows = gaps.rows if gaps else []
    return {
        "keywords": brand.rows if brand else [],
        "source": (brand.meta or {}).get("source", "") if brand else "",
        "search_console": search_console_queries(program.organization),
        "gaps": gap_rows,
        "new_gaps": sum(1 for g in gap_rows if g.get("new")),
        "competitors": (gaps.meta or {}).get("competitors", []) if gaps else [],
        "fetched_at": gaps.fetched_at.isoformat() if gaps else None,
        "refreshing": state["refreshing"],
        "notes": notes,
    }
