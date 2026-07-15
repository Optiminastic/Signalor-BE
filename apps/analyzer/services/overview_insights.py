"""
Overview AI insights: one LLM call over the combined analyzer + GA4 + GSC signals
that returns (a) plain-language insights and (b) concrete SEO/GEO actions. The
actions are persisted as tagged ``Recommendation`` rows (source="ai_insight") so
they surface, and stay filterable, on the Tasks page.

Cached per run (mirrors services/brand_kit.py). A caller forces regeneration with
``force=True``; on LLM failure the last-good report is kept.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.utils.text import slugify

from apps.analyzer.models import AnalysisRun, Recommendation
from apps.analyzer.pipeline.llm import ask_llm

from .overview_signals import build_overview_signals

logger = logging.getLogger("apps")

_VALID_PILLARS = {"content", "schema", "eeat", "technical", "entity", "ai_visibility", "analytics"}
_VALID_PRIORITIES = {p.value for p in Recommendation.Priority}
_VALID_SEVERITIES = {"high", "medium", "low"}


class OverviewInsightError(Exception):
    """Raised when insights can't be generated AND no saved report exists."""


def get_or_generate(run: AnalysisRun, *, force: bool = False) -> dict[str, Any]:
    from apps.analyzer.models import OverviewInsightReport

    if not force:
        existing = OverviewInsightReport.objects.filter(analysis_run=run).first()
        if existing and existing.payload:
            return existing.payload

    signals = build_overview_signals(run)
    raw = ask_llm(
        _build_prompt(signals),
        preferred_provider="gemini",
        max_tokens=1800,
        temperature=0.3,
        purpose=f"overview_insights:run={run.pk}",
    )
    parsed = _parse_json(raw)
    if parsed is None:
        existing = OverviewInsightReport.objects.filter(analysis_run=run).first()
        if existing and existing.payload:
            return existing.payload
        raise OverviewInsightError("LLM returned an unparseable insights response.")

    payload = _normalize(parsed, signals)
    OverviewInsightReport.objects.update_or_create(
        analysis_run=run,
        defaults={
            "payload": payload,
            "has_ga": payload["has_ga"],
            "has_gsc": payload["has_gsc"],
        },
    )
    _persist_tasks(run, payload["tasks"])
    return payload


# ── prompt ───────────────────────────────────────────────────────────────────


def _build_prompt(signals: dict) -> str:
    has_ga = signals["flags"]["has_ga"]
    has_gsc = signals["flags"]["has_gsc"]
    connected = []
    if has_ga:
        connected.append("Google Analytics")
    if has_gsc:
        connected.append("Search Console")
    conn_note = (
        f"Connected analytics sources: {', '.join(connected)}."
        if connected
        else "No analytics connected — use ONLY the GEO/analyzer signals; do not invent traffic numbers."
    )

    return f"""You are an SEO/GEO (generative-engine-optimization) strategist. Analyze the signals
below for one brand and produce specific, evidence-grounded insights and ACTIONS the team can take
to improve search and AI visibility.

{conn_note}

SIGNALS (JSON):
{json.dumps(signals, indent=2)}

Return ONLY a JSON object with this EXACT shape (no prose, no markdown fences):

{{
  "summary": {{
    "headline": "One sentence — the single most important takeaway.",
    "key_points": ["2 to 4 short bullet strings"]
  }},
  "insights": [
    {{
      "title": "Short insight title",
      "detail": "1-2 sentences explaining what the data shows.",
      "severity": "high | medium | low",
      "evidence": "Name the exact signal(s) this is based on, e.g. 'GSC: 8000 impressions, 0.6% CTR'."
    }}
  ],
  "tasks": [
    {{
      "title": "Imperative action title (e.g. 'Improve CTR on /pricing')",
      "description": "Why this matters, grounded in a specific signal.",
      "action": "The concrete change to make.",
      "pillar": "content | schema | eeat | technical | entity | ai_visibility | analytics",
      "priority": "critical | high | medium | low",
      "impact_estimate": "e.g. '+5-10% organic CTR'",
      "why": "One short line on the expected outcome.",
      "steps": ["2 to 5 concrete sub-steps"]
    }}
  ]
}}

Rules:
- Ground EVERY insight and task in a named signal from the data. Do NOT invent metrics or facts.
- Max 6 insights and max 6 tasks. Prefer the highest-impact items.
- Tasks must be specific and actionable, not generic advice.
- Map each task to the closest pillar; use "analytics" for traffic/indexing/CTR-driven actions.
- Return ONLY the JSON object.
"""


# ── parse / normalize ────────────────────────────────────────────────────────


def _parse_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    # Epic 8: shared extractor handles fences/chatty text (was a local fence-stripper).
    from apps.analyzer.pipeline.structured import extract_json

    data = extract_json(raw, expect=dict)
    if not isinstance(data, dict):
        logger.warning("overview_insights JSON parse failed; raw=%r", (raw or "")[:300])
        return None
    return data if isinstance(data, dict) else None


def _str(v, max_len: int) -> str:
    return (v if isinstance(v, str) else "").strip()[:max_len]


def _str_list(v, *, max_items: int, max_len: int) -> list[str]:
    if not isinstance(v, list):
        return []
    out = []
    for item in v:
        s = _str(item, max_len)
        if s:
            out.append(s)
        if len(out) >= max_items:
            break
    return out


def _normalize(data: dict, signals: dict) -> dict[str, Any]:
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    insights = []
    for it in (data.get("insights") or [])[:6]:
        if not isinstance(it, dict):
            continue
        title = _str(it.get("title"), 120)
        if not title:
            continue
        sev = it.get("severity")
        insights.append(
            {
                "title": title,
                "detail": _str(it.get("detail"), 400),
                "severity": sev if sev in _VALID_SEVERITIES else "medium",
                "evidence": _str(it.get("evidence"), 200),
            }
        )

    tasks = []
    for t in (data.get("tasks") or [])[:6]:
        if not isinstance(t, dict):
            continue
        title = _str(t.get("title"), 200)
        if not title:
            continue
        pillar = t.get("pillar")
        priority = t.get("priority")
        tasks.append(
            {
                "title": title,
                "description": _str(t.get("description"), 1000),
                "action": _str(t.get("action"), 1000),
                "pillar": pillar if pillar in _VALID_PILLARS else "technical",
                "priority": priority if priority in _VALID_PRIORITIES else "medium",
                "impact_estimate": _str(t.get("impact_estimate"), 100),
                "why": _str(t.get("why"), 200),
                "steps": _str_list(t.get("steps"), max_items=5, max_len=300),
            }
        )

    return {
        "summary": {
            "headline": _str(summary.get("headline"), 240),
            "key_points": _str_list(summary.get("key_points"), max_items=4, max_len=200),
        },
        "insights": insights,
        "tasks": tasks,
        "has_ga": signals["flags"]["has_ga"],
        "has_gsc": signals["flags"]["has_gsc"],
    }


# ── persist tasks as tagged recommendations ──────────────────────────────────


def _persist_tasks(run: AnalysisRun, tasks: list[dict]) -> None:
    """Replace this run's AI-insight recs with the freshly generated ones. Only
    touches source="ai_insight" rows — analyzer recs are never affected."""
    run.recommendations.filter(source=Recommendation.Source.AI_INSIGHT).delete()
    rows = []
    seen: set[str] = set()
    for t in tasks:
        key = f"ai_insight:{slugify(t['title'])[:60]}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            Recommendation(
                analysis_run=run,
                pillar=t["pillar"],
                priority=t["priority"],
                title=t["title"][:255],
                description=t["description"],
                action=t["action"],
                impact_estimate=t["impact_estimate"],
                category="analytics",
                why=t["why"],
                steps=t["steps"],
                finding_key=key,
                source=Recommendation.Source.AI_INSIGHT,
            )
        )
    if rows:
        Recommendation.objects.bulk_create(rows)
