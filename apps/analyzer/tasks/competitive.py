"""Competitive prompt generation and scoring."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..models import (
    AnalysisRun,
    PromptTrack,
)
from ..pipeline.aggregator import compute_static_composite
from ..pipeline.content import score_content
from ..pipeline.crawler import crawl_page
from ..pipeline.eeat import score_eeat
from ..pipeline.schema import score_schema
from ..pipeline.technical import score_technical

# Imported as modules, not names: a bare `from .accounting import x`
# binds at import time and makes `patch.object(accounting, 'x')` a no-op.
from . import accounting  # noqa: F401

logger = logging.getLogger("apps")


def _score_competitor_static(url: str) -> tuple[dict | None, float]:
    """Score a competitor using STATIC-ONLY pillars (no LLM calls)."""
    crawl = crawl_page(url)
    if not crawl.ok:
        return None, 0.0

    content_score, content_details = score_content(crawl)
    schema_score_val, schema_details = score_schema(crawl)
    # Use static-only E-E-A-T (skip_gemini=True)
    eeat_score_val, eeat_details = score_eeat(crawl, skip_gemini=True)
    technical_score_val, technical_details = score_technical(crawl)

    composite = compute_static_composite(content_score, schema_score_val, eeat_score_val, technical_score_val)

    page_data = {
        "url": url,
        "content_score": content_score,
        "content_details": content_details,
        "schema_score": schema_score_val,
        "schema_details": schema_details,
        "eeat_score": eeat_score_val,
        "eeat_details": eeat_details,
        "technical_score": technical_score_val,
        "technical_details": technical_details,
        "composite_score": composite,
    }

    return page_data, composite


def _domain_label(url: str) -> str:
    """Cheap fallback brand label when AnalysisRun.brand_name is empty."""
    from urllib.parse import urlparse

    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        host = urlparse(raw).netloc.removeprefix("www.")
    except Exception:
        return ""
    return host.split(".")[0] if host else ""


def _fire_competitive_prompt_fast(track: PromptTrack, brand_name: str, brand_url: str) -> None:
    """Light-weight fire for auto-generated competitive prompts. Uses runs=1
    instead of the default 3 (3x fewer LLM calls per prompt) since the
    Competitive Prompts page is a discovery surface, not a precision-ranking
    one — the user values "see prompts and which engines mention competitors"
    over "average across 3 runs"."""
    from django.db import close_old_connections

    from ..pipeline.citations import (
        competitor_hosts_for_run,
        host_of,
        persist_prompt_result,
    )
    from ..pipeline.prompt_tracker import (
        compute_prompt_score,
        fire_prompt_across_engines,
    )

    close_old_connections()
    try:
        engine_results = fire_prompt_across_engines(
            track.prompt_text,
            brand_name,
            brand_url,
            runs=1,  # ← key speed win
            allowed_engines=None,
        )
        brand_host = host_of(brand_url)
        rival_hosts = competitor_hosts_for_run(track.analysis_run)
        for r in engine_results:
            persist_prompt_result(track, r, brand_host, rival_hosts)

        all_res = list(
            track.results.values("brand_mentioned", "sentiment", "rank_position", "confidence", "engine")
        )
        sd = compute_prompt_score(all_res)
        track.score = sd["score"]
        track.authority_score = sd["authority_score"]
        track.content_quality_score = sd["content_quality_score"]
        track.structural_score = sd["structural_score"]
        track.semantic_score = sd["semantic_score"]
        track.third_party_score = sd["third_party_score"]
        track.save(
            update_fields=[
                "score",
                "authority_score",
                "content_quality_score",
                "structural_score",
                "semantic_score",
                "third_party_score",
            ]
        )
    except Exception:
        logger.exception(
            "Fast competitive fire failed for track %d (run %d)",
            track.id,
            track.analysis_run_id,
        )


def _generate_and_fire_competitive_prompts(run: AnalysisRun) -> None:
    """Auto-generate 10 buyer-intent prompts for this brand and fire them
    through the existing engine pipeline. Runs once per AnalysisRun (idempotent
    on prompt_type=COMPETITIVE rows) and dispatches the actual engine fires to
    a background thread pool so it never delays run completion.

    Speed budget: runs=1 (vs default 3) on each prompt fire + 4-way parallel
    pool over the 10 prompts → roughly the time of 3 prompts firing serially.

    Result: the per-brand Competitive Prompt Insights page populates without
    the user having to trigger anything from the UI.
    """
    try:
        # This fires up to 40 billable calls (10 prompts x 4 engines), so it is
        # subject to the same budget as the analysis that triggered it. Without
        # this an account whose allowance is exhausted could still spend
        # unbounded amounts, once per completed run.
        status = accounting._budget_status(run.email)
        if status is not None and not status.allowed:
            logger.warning(
                "Run %d: skipping competitive prompts, %s is over its LLM allowance "
                "($%.2f of $%.2f)",
                run.id,
                status.email or "(anonymous)",
                status.spent_usd,
                status.limit_usd,
            )
            return

        # Idempotency: count-based, not existence-based. Onboarding can plant a
        # single COMPETITIVE-typed prompt (e.g. the hardcoded "Compare X with
        # competitors" fallback from GeneratePromptsView), which would trip a
        # naive .exists() check and skip generation entirely. Our auto-gen
        # always produces 10 prompts, so saturation = >=10 already present.
        existing_competitive = run.prompt_tracks.filter(
            prompt_type=PromptTrack.PromptSurfaceType.COMPETITIVE,
            is_custom=False,
            deleted_at__isnull=True,
        ).count()
        if existing_competitive >= 10:
            logger.info(
                "Competitive prompts at saturation (%d>=10) for run %d; skipping auto-gen.",
                existing_competitive,
                run.id,
            )
            return

        from ..pipeline.prompt_tracker import (
            classify_prompt_intent_and_type,
            generate_brand_prompts,
        )

        brand = (run.brand_name or "").strip() or _domain_label(run.url)
        url = (run.url or "").strip()

        from apps.organizations.services.brand_context import build_context

        prompts = generate_brand_prompts(
            brand_name=brand,
            brand_url=url,
            country=(run.country or "").strip(),
            count=10,
            brand_card=build_context(run),
        )
        if not prompts:
            logger.info("Auto-gen returned no competitive prompts for run %d.", run.id)
            return

        # Persist tracks first so the Prompts page renders the queue immediately
        # while engine fires populate results asynchronously.
        tracks: list[PromptTrack] = []
        for prompt_text in prompts:
            intent, _ = classify_prompt_intent_and_type(prompt_text, brand, url)
            tracks.append(
                PromptTrack.objects.create(
                    analysis_run=run,
                    prompt_text=prompt_text,
                    is_custom=False,
                    intent=intent,
                    prompt_type=PromptTrack.PromptSurfaceType.COMPETITIVE,
                )
            )

        from core.llm.client import cost_scope, propagate

        def _fire_all():
            # This work happens after the run was finalized and its logs drained,
            # so it cannot ride on the run's collection window. A cost scope meters
            # it independently and the total is added to the run afterwards, which
            # is what puts it inside the 30-day window the budget fuse reads.
            with cost_scope() as spend:
                # Parallel pool: 4 prompts in flight, each one fires its engines
                # in parallel internally. Keeps total wall-clock to ~3x slowest
                # engine round-trip instead of 10x.
                with ThreadPoolExecutor(max_workers=4) as pool:
                    # Pool workers do not inherit contextvars, so the scope and the
                    # Langfuse run identity have to be carried across explicitly.
                    futures = [
                        pool.submit(propagate(_fire_competitive_prompt_fast), t, brand, url)
                        for t in tracks
                    ]
                    for fut in as_completed(futures):
                        try:
                            fut.result()
                        except Exception:
                            # _fire_competitive_prompt_fast already logs internally.
                            pass
            accounting._add_background_spend(run.id, spend)

        threading.Thread(target=propagate(_fire_all), daemon=True).start()
        logger.info(
            "Queued %d competitive prompts for run %d (runs=1, pool=4).",
            len(tracks),
            run.id,
        )

    except Exception:
        logger.exception("Auto-competitive prompt generation failed for run %d", run.id)

