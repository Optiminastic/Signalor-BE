"""
Assemble a small, structured signal payload for the Overview AI-insights LLM call.

Blends the run's GEO/analyzer scores with the org's latest Google Analytics (GA4)
and Search Console (GSC) snapshots. Reads CACHED snapshots only (no live fetch) and
re-derives the org from the run server-side — the caller never supplies it.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.analyzer.models import AnalysisRun

logger = logging.getLogger("apps")


def _latest_ga(org):
    """Latest complete-enough GA4 snapshot for the org, or None."""
    from apps.integrations.models import Integration

    integ = Integration.objects.filter(
        organization=org,
        provider=Integration.Provider.GOOGLE_ANALYTICS,
        is_active=True,
    ).first()
    if not integ:
        return None
    return integ.ga_snapshots.first()  # ordering -created_at


def _latest_gsc(org):
    from apps.integrations.models import Integration

    integ = Integration.objects.filter(
        organization=org,
        provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE,
        is_active=True,
    ).first()
    if not integ:
        return None, None
    return integ.gsc_snapshots.first(), integ.gsc_index_snapshots.filter(sync_status="complete").first()


def _trend_direction(daily, key: str) -> str:
    """'up' / 'down' / 'flat' comparing the first vs last point of a daily series."""
    pts = [p for p in (daily or []) if isinstance(p, dict)]
    if len(pts) < 2:
        return "flat"
    first = float(pts[0].get(key, 0) or 0)
    last = float(pts[-1].get(key, 0) or 0)
    if last > first * 1.05:
        return "up"
    if last < first * 0.95:
        return "down"
    return "flat"


def _geo_signals(run: AnalysisRun) -> dict[str, Any]:
    page = run.page_scores.order_by("-composite_score").first()
    pillars = {}
    if page:
        pillars = {
            "content": round(page.content_score, 1),
            "schema": round(page.schema_score, 1),
            "eeat": round(page.eeat_score, 1),
            "technical": round(page.technical_score, 1),
            "entity": round(page.entity_score, 1),
            "ai_visibility": round(page.ai_visibility_score, 1),
        }
    recs = [
        {"title": r.title, "pillar": r.pillar, "priority": r.priority}
        for r in run.recommendations.filter(source="analyzer").order_by("priority")[:8]
    ]
    return {
        "composite_score": round(run.composite_score, 1) if run.composite_score is not None else None,
        "pillar_scores": pillars,
        "top_analyzer_findings": recs,
    }


def _ga_signals(snap) -> dict[str, Any] | None:
    if not snap:
        return None
    return {
        "sessions": snap.sessions,
        "organic_sessions": snap.organic_sessions,
        "organic_pct": round(100 * snap.organic_sessions / snap.sessions, 1) if snap.sessions else 0,
        "bounce_rate": round(snap.bounce_rate, 1),
        "avg_session_duration": round(snap.avg_session_duration, 1),
        "sessions_trend": _trend_direction(snap.daily_trend, "sessions"),
        "top_pages": [
            {"path": p.get("path", ""), "sessions": p.get("sessions", 0)} for p in (snap.top_pages or [])[:5]
        ],
        "top_sources": [
            {"source": s.get("source", ""), "sessions": s.get("sessions", 0)}
            for s in (snap.traffic_sources or [])[:5]
        ],
        "date_start": str(snap.date_start),
        "date_end": str(snap.date_end),
    }


def _gsc_signals(snap, index_snap) -> dict[str, Any] | None:
    if not snap and not index_snap:
        return None
    out: dict[str, Any] = {}
    if snap:
        out.update(
            {
                "clicks": snap.clicks,
                "impressions": snap.impressions,
                "ctr": round(snap.ctr, 4),
                "position": round(snap.position, 1),
                "clicks_trend": _trend_direction(snap.daily_trend, "clicks"),
                "top_queries": [
                    {
                        "query": q.get("query", ""),
                        "clicks": q.get("clicks", 0),
                        "impressions": q.get("impressions", 0),
                        "position": q.get("position", 0),
                    }
                    for q in (snap.top_queries or [])[:8]
                ],
                "top_pages": [
                    {"page": p.get("page", ""), "clicks": p.get("clicks", 0)}
                    for p in (snap.top_pages or [])[:5]
                ],
            }
        )
    if index_snap:
        out["indexed_count"] = index_snap.indexed_count
        out["not_indexed_count"] = index_snap.not_indexed_count
        out["checked_count"] = index_snap.checked_count
    return out


def build_overview_signals(run: AnalysisRun) -> dict[str, Any]:
    """Compact, LLM-ready signal bundle for one run. GA/GSC are None when absent."""
    org = run.organization
    ga_snap = _latest_ga(org) if org else None
    gsc_snap, gsc_index = _latest_gsc(org) if org else (None, None)

    ga = _ga_signals(ga_snap)
    gsc = _gsc_signals(gsc_snap, gsc_index)

    return {
        "brand": {
            "name": run.brand_name or "",
            "url": run.url or "",
            "country": run.country or "",
        },
        "geo": _geo_signals(run),
        "ga": ga,
        "gsc": gsc,
        "flags": {"has_ga": ga is not None, "has_gsc": gsc is not None},
    }
