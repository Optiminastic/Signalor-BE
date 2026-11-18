"""
The brand as the agent should present it: its real name and grounded facts.

Organizations are often named loosely in Signalor (e.g. "cc"), so the display
name prefers what the analysis detected on the site. Facts come from the
brand's knowledge base; when retrieval is empty or unavailable (embedding
errors), they fall back to the brand card built from the approved profile or
the latest analysis, so copy is never written with zero brand context.
"""

from __future__ import annotations

from apps.analyzer.models import AnalysisRun
from apps.organizations.services.brand_context import build_context
from apps.organizations.services.retrieval import build_knowledge_block

from ..positioning.inputs import brand_entity_details

MIN_NAME_LENGTH = 3


def latest_complete_run(organization) -> AnalysisRun | None:
    return (
        AnalysisRun.objects.filter(organization=organization, status=AnalysisRun.Status.COMPLETE)
        .order_by("-created_at")
        .first()
    )


def display_name(organization) -> str:
    run = latest_complete_run(organization)
    if run is not None:
        detected = (brand_entity_details(run).get("checks") or {}).get("brand_name", "").strip()
        for candidate in (detected, (run.brand_name or "").strip()):
            if len(candidate) >= MIN_NAME_LENGTH:
                return candidate
    return organization.name


def brand_card(organization) -> str:
    run = latest_complete_run(organization)
    return build_context(run or organization)


def brand_facts(organization, query: str, max_chars: int) -> str:
    """Knowledge-base passages for ``query``, else the brand card. Same text must
    go to the writer and to the fact check."""
    knowledge = build_knowledge_block(organization, query, max_chars=max_chars)
    return knowledge or brand_card(organization)[:max_chars]
