"""GEO-signal task generator.

The static finding engine only sees on-page HTML. It is blind to what the
platform actually measures about generative-engine visibility: which tracked
prompts the brand is *not* cited for, which competitors win those prompts, and
which citation domains recur without the brand. This module turns that measured
signal into high-value, grounded tasks.

Each task's impact is a real observed statistic (e.g. "cited in 0/5 tracked
prompts; a competitor is cited in 4"), never a fabricated point value. Tasks are
persisted as ``Recommendation`` rows with ``source = geo_signal`` and refreshed
idempotently, mirroring ``services/overview_insights`` (source=ai_insight).

Graceful degradation: if no prompt-tracking results exist yet (e.g. the first
run, before prompts have fired), the generator returns ``[]`` - no error.
"""

from __future__ import annotations

import logging

from apps.analyzer.pipeline.aggregator import get_weights

logger = logging.getLogger("apps")

# Synthetic, stable finding codes so verify/reprioritize routing and dedupe work.
CODE_PROMPT_LOST = "geo_prompt_lost"
CODE_COMPETITOR_CITED = "geo_competitor_cited"
CODE_CITATION_GAP = "geo_citation_gap"
CODE_COMPETITOR_PILLAR_GAP = "geo_competitor_pillar_gap"

# How many tasks to emit per category, highest-signal first.
_MAX_LOST_PROMPTS = 3
_MAX_CITATION_GAPS = 2


def _impact_points(pillar: str, severity: float, industry: str) -> float:
    """A grounded composite hint: pillar weight x observed severity (0..1)."""
    weight = get_weights(industry).get(pillar, 0.0)
    return round(weight * 100.0 * max(0.0, min(1.0, severity)), 2)


def _base(finding_code: str, pillar: str, priority: str) -> dict:
    """Common Recommendation kwargs for a GEO task."""
    from apps.analyzer.models import Recommendation

    return {
        "pillar": pillar,
        "priority": priority,
        "category": pillar,
        "finding_code": finding_code,
        "finding_key": finding_code,
        "source": Recommendation.Source.GEO_SIGNAL,
        "why": "AI engines cite trusted, well-represented brands - measured directly from live prompts.",
        "steps": [],
        "affected_pages": [],
    }


def generate_geo_signal_tasks(run, industry: str = "default") -> list[dict]:
    """Build GEO-signal recommendation dicts from a run's measured prompt data.

    Returns a list of ``Recommendation`` kwargs (no DB writes). Empty when the run
    has no prompt-tracking results yet.
    """
    from apps.analyzer.models import PromptTrack

    tracks = list(
        PromptTrack.objects.filter(analysis_run=run, deleted_at__isnull=True)
        .prefetch_related("results", "results__citations")
    )

    tasks: list[dict] = []
    lost: list[tuple[PromptTrack, list[str]]] = []  # (track, engines that missed)
    competitor_domains: dict[str, int] = {}
    gap_domains: dict[str, int] = {}

    for track in tracks:
        results = list(track.results.all())
        if not results:
            continue  # never fired -> unknown, not "lost"
        mentioned = [r for r in results if r.brand_mentioned]
        if mentioned:
            continue  # brand appears somewhere -> not a loss
        engines = sorted({r.get_engine_display() for r in results})
        lost.append((track, engines))
        # Collect who *is* cited when the brand is absent.
        for r in results:
            for c in r.citations.all():
                if c.is_brand:
                    continue
                if c.is_competitor and c.domain:
                    competitor_domains[c.domain] = competitor_domains.get(c.domain, 0) + 1
                elif c.domain:
                    gap_domains[c.domain] = gap_domains.get(c.domain, 0) + 1

    total = len(tracks)
    lost_count = len(lost)

    # ── 1. Prompts the brand is losing (highest value) ──
    for track, engines in lost[:_MAX_LOST_PROMPTS]:
        prompt = track.prompt_text.strip()
        engine_list = ", ".join(engines)
        task = _base(CODE_PROMPT_LOST, "ai_visibility", "high")
        task.update({
            "title": f"Win the AI query: \"{prompt[:80]}\"",
            "description": (
                f"You are not cited for this tracked prompt on {engine_list}. "
                f"This is a real query buyers ask - and AI engines answer it with other sources."
            ),
            "action": (
                "Publish an answer-first page that directly and comprehensively answers this "
                "exact query: lead with a 2-3 sentence direct answer, add a comparison table and "
                "FAQ, cite authoritative sources, and mark it up with FAQPage/Article schema. "
                "Then re-check visibility for this prompt."
            ),
            "impact_estimate": f"Cited in 0/{total} results for this prompt across {len(engines)} engine(s)",
            "evidence": {"prompt": prompt, "engines": engines, "brand_mentions": 0},
            "impact_points": _impact_points("ai_visibility", 0.9, industry),
        })
        tasks.append(task)

    # ── 2. Competitors cited where the brand is absent ──
    if competitor_domains:
        top = sorted(competitor_domains.items(), key=lambda kv: -kv[1])[:5]
        names = ", ".join(d for d, _ in top[:3])
        task = _base(CODE_COMPETITOR_CITED, "ai_visibility", "high")
        task.update({
            "title": "Close the citation gap with competitors AI recommends",
            "description": (
                f"On prompts where you are absent, AI engines cite competitors such as {names}. "
                f"These sources are winning the recommendation you should own."
            ),
            "action": (
                "Study what those competitor pages do that you don't (depth, comparisons, reviews, "
                "schema, third-party mentions), then close the gap on your equivalent pages and "
                "earn mentions on the same high-authority sources."
            ),
            "impact_estimate": f"{len(competitor_domains)} competitor domain(s) cited across your lost prompts",
            "evidence": {"competitor_domains": dict(top)},
            "impact_points": _impact_points("ai_visibility", 0.7, industry),
        })
        tasks.append(task)

    # ── 3. Recurring citation-domain gaps (get mentioned there) ──
    recurring = sorted(
        ((d, n) for d, n in gap_domains.items() if n >= 2),
        key=lambda kv: -kv[1],
    )[:_MAX_CITATION_GAPS]
    for domain, count in recurring:
        task = _base(CODE_CITATION_GAP, "entity", "medium")
        task.update({
            "title": f"Get mentioned on {domain}",
            "description": (
                f"AI engines repeatedly cite {domain} ({count}x) when answering your tracked "
                f"prompts, but never cite you. Earning a presence there feeds AI responses."
            ),
            "action": (
                f"Pursue a legitimate mention on {domain} - a listing, guest contribution, review, "
                f"or editorial mention as appropriate for that source. AI re-indexes these regularly."
            ),
            "impact_estimate": f"{domain} cited {count}x across your lost prompts; brand cited 0x",
            "evidence": {"domain": domain, "citations": count},
            "impact_points": _impact_points("entity", 0.5, industry),
        })
        tasks.append(task)

    # ── 4. Competitor pillar gap ──
    pillar_gap_task = _competitor_pillar_gap_task(run, industry)
    if pillar_gap_task is not None:
        tasks.append(pillar_gap_task)

    logger.info(
        "Run %s: generated %d GEO-signal tasks (%d/%d prompts lost)",
        getattr(run, "id", "?"), len(tasks), lost_count, total,
    )
    return tasks


def _competitor_pillar_gap_task(run, industry: str) -> dict | None:
    """Emit a task when a competitor materially out-scores the brand's homepage."""
    from apps.analyzer.models import Competitor, PageScore

    ps = PageScore.objects.filter(analysis_run=run).order_by("id").first()
    if not ps or not ps.composite_score:
        return None
    comps = list(
        Competitor.objects.filter(analysis_run=run, scored=True, composite_score__isnull=False)
        .order_by("-composite_score")
    )
    if not comps:
        return None
    top = comps[0]
    gap = float(top.composite_score) - float(ps.composite_score)
    if gap < 8.0:  # not a material gap
        return None
    task = _base(CODE_COMPETITOR_PILLAR_GAP, "ai_visibility", "medium")
    task.update({
        "title": f"Outrank {top.name} on AI-readiness",
        "description": (
            f"{top.name} scores {top.composite_score:.0f} vs your {ps.composite_score:.0f} on "
            f"overall AI-readiness - a {gap:.0f}-point gap that shapes which brand AI recommends."
        ),
        "action": (
            "Prioritise the pillars where the gap is widest (usually content depth, E-E-A-T, and "
            "schema). Complete the on-page fixes in your task list, then re-analyse to confirm the "
            "gap has closed."
        ),
        "impact_estimate": f"{gap:.0f}-point AI-readiness gap vs {top.name}",
        "evidence": {"competitor": top.name, "competitor_score": round(float(top.composite_score), 1),
                     "brand_score": round(float(ps.composite_score), 1), "gap": round(gap, 1)},
        "impact_points": _impact_points("ai_visibility", min(1.0, gap / 40.0), industry),
    })
    return task


def sync_geo_signal_tasks(run, industry: str = "default") -> int:
    """Idempotently replace this run's GEO-signal Recommendations. Returns count.

    Safe to call repeatedly (e.g. after each prompt re-check): existing geo_signal
    rows for the run are removed and rebuilt from current measurements. On-page
    (analyzer) and ai_insight recommendations are untouched.
    """
    from django.db import transaction

    from apps.analyzer.models import Recommendation, UserAction

    tasks = generate_geo_signal_tasks(run, industry=industry)
    with transaction.atomic():
        # Remove materialized tasks for the old GEO recs first: UserAction.recommendation
        # is SET_NULL, so deleting the recs alone would orphan (not remove) their tasks.
        UserAction.objects.filter(
            analysis_run=run, recommendation__source=Recommendation.Source.GEO_SIGNAL
        ).delete()
        run.recommendations.filter(source=Recommendation.Source.GEO_SIGNAL).delete()
        if tasks:
            Recommendation.objects.bulk_create(
                [Recommendation(analysis_run=run, **t) for t in tasks]
            )
    return len(tasks)
