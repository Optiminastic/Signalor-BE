"""30-day AI-visibility projection for the dashboard overview.

A forward-looking estimate of where a brand's AI search visibility can get to
in the next 30 days, given two honest signals:

* **Momentum** - the recent daily trend across the brand's own completed runs.
* **Opportunity** - the concrete work still open: weak prompts to strengthen
  and competitors sitting just ahead.

Every number is an *estimate*, not a promise. Gains are deliberately capped by
the remaining headroom (100 - current score) so the dashboard never projects a
brand past 100% or over-promises a result it cannot plausibly reach.

The endpoint that serves this is slug-scoped like every other run read on the
overview; see ``views/projection.py``.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from ..models import AnalysisRun, PromptResult, PromptTrack

WINDOW_DAYS = 30

# Share of the remaining headroom we treat as reachable in a month from pure
# momentum - a brand climbing fast still can't clear all the remaining gap in 30 days.
_MOMENTUM_HEADROOM_CAP = 0.6
# Only half of the raw momentum carries forward: recent slope regresses toward the mean.
_MOMENTUM_CARRY = 0.5
# Weight on the "open opportunity" uplift (the share of prompts still weak).
_OPPORTUNITY_WEIGHT = 0.4
# A brand with real headroom and open work should show a non-trivial floor.
_MIN_GAIN = 3
# Default confidence for the recommendation lift when there are no prompts to gauge.
_DEFAULT_OPP_RATIO = 0.5


def _brand_visibility(run: AnalysisRun) -> int:
    """The brand's current 0-100 visibility, mirroring RankingsView's fallback."""
    score = run.composite_score
    if score is None:
        brand_vis = getattr(run, "brand_visibility", None)
        score = brand_vis.overall_score if brand_vis is not None else None
    return round(score or 0)


def _sibling_runs(run: AnalysisRun):
    """The brand's own completed runs, scoped by organization when present else email."""
    if run.organization_id:
        return AnalysisRun.objects.filter(organization_id=run.organization_id, status="complete")
    if run.email:
        return AnalysisRun.objects.filter(email=run.email, status="complete")
    return AnalysisRun.objects.filter(pk=run.pk, status="complete")


def _momentum_per_day(run: AnalysisRun) -> float:
    """Average daily change in composite score across recent completed runs.

    Returns 0.0 when there is only one data point (no trend to read).
    """
    cutoff = timezone.now() - timedelta(days=WINDOW_DAYS)
    points = list(
        _sibling_runs(run)
        .filter(updated_at__gte=cutoff)
        .order_by("updated_at")
        .values_list("updated_at", "composite_score")
    )
    if len(points) < 2:
        return 0.0
    (first_at, first_score), (last_at, last_score) = points[0], points[-1]
    span_days = max(1.0, (last_at - first_at).total_seconds() / 86400)
    return ((last_score or 0) - (first_score or 0)) / span_days


def _recommendation_pct(run: AnalysisRun) -> int:
    """Current recommendation rate: positive brand mentions over all prompt results.

    Mirrors the aggregation in ``AiRecommendationSummaryView`` so the projection's
    baseline matches the number the dashboard already shows.
    """
    totals = PromptResult.objects.filter(
        prompt_track__analysis_run=run,
        prompt_track__deleted_at__isnull=True,
    ).aggregate(
        total=Count("id"),
        recommended=Count(
            "id",
            filter=Q(brand_mentioned=True, sentiment=PromptResult.Sentiment.POSITIVE),
        ),
    )
    total = totals["total"] or 0
    if not total:
        return 0
    return round(totals["recommended"] / total * 100)


def _weak_prompt_count(scores: list[float]) -> int:
    """Prompts scoring below the midpoint. Scale-agnostic: some runs store 0-1,
    others 0-100, so the threshold is derived from the observed maximum."""
    if not scores:
        return 0
    scale = 100.0 if max(scores) > 1.0 else 1.0
    threshold = 0.5 * scale
    return sum(1 for score in scores if (score or 0) < threshold)


def _competitors_ahead(run: AnalysisRun, current_vis: int) -> list[tuple[str, int]]:
    """(name, visibility) for rivals currently scoring above the brand, weakest first."""
    ahead: list[tuple[str, int]] = []
    for competitor in run.competitors.all():
        vis = competitor.composite_score
        if vis is None:
            vis = competitor.relevance_score
        vis = round(vis or 0)
        if vis > current_vis:
            ahead.append((competitor.name or "Competitor", vis))
    ahead.sort(key=lambda row: row[1])
    return ahead


def _projected_visibility_gain(headroom: int, momentum_per_day: float, opp_ratio: float) -> int:
    """Momentum carried forward plus the open-opportunity uplift, capped by headroom."""
    momentum_gain = max(0.0, momentum_per_day * WINDOW_DAYS)
    momentum_gain = min(momentum_gain * _MOMENTUM_CARRY, headroom * _MOMENTUM_HEADROOM_CAP)
    opportunity_gain = headroom * _OPPORTUNITY_WEIGHT * opp_ratio
    gain = momentum_gain + opportunity_gain
    if headroom > 0:
        gain = max(gain, min(_MIN_GAIN, headroom))
    return round(min(gain, headroom))


def build_projection(run: AnalysisRun) -> dict:
    """Assemble the 30-day projection payload for one completed run."""
    current_vis = _brand_visibility(run)
    headroom = max(0, 100 - current_vis)

    prompt_scores = list(
        PromptTrack.objects.filter(analysis_run=run, deleted_at__isnull=True).values_list(
            "score", flat=True
        )
    )
    total_prompts = len(prompt_scores)
    weak_prompts = _weak_prompt_count(prompt_scores)
    opp_ratio = (weak_prompts / total_prompts) if total_prompts else 0.0

    projected_gain = _projected_visibility_gain(headroom, _momentum_per_day(run), opp_ratio)
    vis_target = min(100, current_vis + projected_gain)

    ahead = _competitors_ahead(run, current_vis)
    to_pass = [name for name, vis in ahead if vis <= vis_target]

    rec_current = _recommendation_pct(run)
    rec_headroom = max(0, 100 - rec_current)
    rec_gain = round(rec_headroom * _OPPORTUNITY_WEIGHT * (opp_ratio or _DEFAULT_OPP_RATIO))
    rec_target = min(100, rec_current + rec_gain)

    return {
        "window_days": WINDOW_DAYS,
        "generated_at": timezone.now().isoformat(),
        "visibility": {
            "current": current_vis,
            "target": vis_target,
            "delta": vis_target - current_vis,
        },
        "recommendation": {
            "current": rec_current,
            "target": rec_target,
            "delta": rec_target - rec_current,
        },
        "competitors": {
            "to_pass": len(to_pass),
            "names": to_pass[:5],
            "total_ahead": len(ahead),
        },
        "prompts": {"to_improve": weak_prompts, "total": total_prompts},
    }
