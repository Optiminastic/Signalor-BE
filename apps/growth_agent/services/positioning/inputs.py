"""
Plain data the positioning report is computed from.

``collect_inputs`` is the only function here that touches the database; it
snapshots one completed analysis into frozen dataclasses so the metrics in
``metrics.py`` stay pure and easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

from apps.analyzer.models import AnalysisRun, PageScore, PromptResult


@dataclass(frozen=True)
class Citation:
    domain: str
    url: str
    title: str


@dataclass(frozen=True)
class EngineAnswer:
    prompt_id: int
    prompt_text: str
    engine: str
    response_text: str
    brand_mentioned: bool
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True)
class Rival:
    name: str
    domain: str


@dataclass(frozen=True)
class PositioningInputs:
    brand_names: tuple[str, ...]
    brand_domain: str
    answers: tuple[EngineAnswer, ...]
    rivals: tuple[Rival, ...]
    entity_checks: dict = field(default_factory=dict)
    entity_findings: tuple[str, ...] = ()


def bare_domain(url_or_domain: str) -> str:
    raw = (url_or_domain or "").strip().lower()
    if not raw:
        return ""
    host = urlparse(raw if "://" in raw else f"https://{raw}").hostname or ""
    return host.removeprefix("www.")


def brand_entity_details(run: AnalysisRun) -> dict:
    page = (
        PageScore.objects.filter(analysis_run=run, competitor__isnull=True)
        .exclude(entity_details={})
        .order_by("id")
        .first()
    )
    return page.entity_details if page else {}


def _brand_names(run: AnalysisRun, entity_checks: dict) -> tuple[str, ...]:
    candidates = [entity_checks.get("brand_name", ""), run.brand_name]
    if run.organization_id:
        candidates.append(run.organization.name)
    seen: list[str] = []
    for name in candidates:
        cleaned = (name or "").strip()
        if len(cleaned) >= 3 and cleaned.lower() not in {s.lower() for s in seen}:
            seen.append(cleaned)
    return tuple(seen)


def _answers(run: AnalysisRun, since: datetime | None = None) -> tuple[EngineAnswer, ...]:
    results = PromptResult.objects.filter(
        prompt_track__analysis_run=run, prompt_track__deleted_at__isnull=True
    )
    if since is not None:
        results = results.filter(checked_at__gte=since)
    results = (
        results.select_related("prompt_track")
        .prefetch_related("citations")
        .order_by("prompt_track_id", "engine", "id")
    )
    return tuple(
        EngineAnswer(
            prompt_id=result.prompt_track_id,
            prompt_text=result.prompt_track.prompt_text,
            engine=result.engine,
            response_text=result.response_text or "",
            brand_mentioned=result.brand_mentioned,
            citations=tuple(
                Citation(domain=bare_domain(c.domain or c.url), url=c.url, title=c.title or "")
                for c in result.citations.all()
            ),
        )
        for result in results
    )


def collect_inputs(run: AnalysisRun, since: datetime | None = None) -> PositioningInputs:
    """Snapshot one analysis. ``since`` limits answers to those checked after it
    (a weekly re-check appends fresh answers to the same prompts)."""
    details = brand_entity_details(run)
    checks = details.get("checks") or {}
    rivals = tuple(
        Rival(name=c.name, domain=bare_domain(c.url)) for c in run.competitors.all().order_by("id")
    )
    return PositioningInputs(
        brand_names=_brand_names(run, checks),
        brand_domain=bare_domain(run.url),
        answers=_answers(run, since),
        rivals=rivals,
        entity_checks=checks,
        entity_findings=tuple(details.get("findings") or ()),
    )
