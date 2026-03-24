import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from .models import (
    AIVisibilityProbe,
    AnalysisRun,
    BrandVisibility,
    Competitor,
    PageScore,
    Recommendation,
    PromptTrack,
    PromptResult,
)
from .pipeline.brand_visibility import extract_brand_name, run_brand_visibility
from .pipeline.aggregator import compute_composite, compute_static_composite, detect_industry
from .pipeline.ai_visibility import score_ai_visibility
from .pipeline.competitors import discover_competitors
from .pipeline.content import score_content
from .pipeline.crawler import crawl_page, CrawlResult
from .pipeline.eeat import score_eeat
from .pipeline.entity import score_entity
from .pipeline.llm import start_log_collection, get_collected_logs
from .pipeline.recommendations import generate_recommendations
from .pipeline.schema import score_schema
from .pipeline.technical import score_technical

logger = logging.getLogger("apps")


def _crawl_via_integration(run: AnalysisRun) -> CrawlResult | None:
    """Fallback: fetch page content via Shopify/WordPress API when public crawl fails."""
    from apps.integrations.models import Integration
    from bs4 import BeautifulSoup
    from .pipeline.utils import extract_text, extract_internal_links

    if not run.organization:
        return None

    integration = Integration.objects.filter(
        organization=run.organization,
        is_active=True,
        provider__in=["shopify", "wordpress"],
    ).first()

    if not integration:
        return None

    try:
        from .auto_fix import _fetch_page_content
        page_info, html_content = _fetch_page_content(integration, run.url)

        if not page_info or not html_content:
            return None

        # Reject content too short to analyze meaningfully
        if len(html_content.strip()) < 50:
            logger.warning("Run %d: integration content too short (%d chars), skipping", run.id, len(html_content))
            return None

        # Build a CrawlResult from the API content
        soup = BeautifulSoup(html_content, "html.parser")
        text = extract_text(soup)
        result = CrawlResult(
            url=run.url,
            status_code=200,
            html=html_content,
            soup=soup,
            text=text,
            internal_links=extract_internal_links(soup, run.url),
            load_time=0.0,
            error="",
            is_https=run.url.startswith("https"),
        )
        logger.info("Run %d: crawled via %s integration (API fallback, %d chars, %d text chars)",
                     run.id, integration.provider, len(html_content), len(text))
        return result
    except Exception as e:
        logger.warning("Run %d: integration crawl fallback failed: %s", run.id, e)
        return None


def _save_probes_and_tracks(run: AnalysisRun, probes_data: list[dict], brand_name: str, brand_url: str):
    """Save AIVisibilityProbe rows and backfill PromptTrack/PromptResult rows."""
    from .pipeline.prompt_tracker import fire_prompt_across_engines

    for probe in probes_data:
        AIVisibilityProbe.objects.create(analysis_run=run, **probe)

        # Backfill into the new prompt tracking models
        try:
            track = PromptTrack.objects.create(
                analysis_run=run,
                prompt_text=probe["prompt_used"],
                is_custom=False,
            )
            engine_results = fire_prompt_across_engines(probe["prompt_used"], brand_name, brand_url)
            for r in engine_results:
                PromptResult.objects.create(prompt_track=track, **r)
        except Exception as exc:
            logger.warning("PromptTrack backfill failed for run %d: %s", run.id, exc)


def _update_status(run: AnalysisRun, status: str, progress: int = 0):
    run.status = status
    run.progress = progress
    run.save(update_fields=["status", "progress", "updated_at"])


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

    composite = compute_static_composite(
        content_score, schema_score_val, eeat_score_val, technical_score_val
    )

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


def _run_partial_analysis(run: AnalysisRun, crawl):
    """
    Run partial analysis when crawler fails to get HTML.
    Still checks: robots.txt, sitemap, llms.txt, HTTPS, load time.
    Also runs entity + AI visibility via LLM (don't need HTML).
    """
    logger.info("Run %d: crawl failed (%s), running partial analysis", run.id, crawl.error)
    start_log_collection()

    _update_status(run, AnalysisRun.Status.ANALYZING, 20)

    # Content, schema, eeat all need HTML — score 0 with explanation
    content_score, content_details = 0.0, {
        "checks": {"crawl_failed": True},
        "findings": [],
        "note": f"Page content could not be accessed: {crawl.error}",
    }
    schema_score_val, schema_details = 0.0, {
        "checks": {"crawl_failed": True},
        "findings": [],
        "note": f"Schema markup could not be checked: {crawl.error}",
    }
    eeat_score_val, eeat_details = 0.0, {
        "checks": {"crawl_failed": True},
        "findings": [],
        "note": f"E-E-A-T signals could not be analyzed: {crawl.error}",
    }

    _update_status(run, AnalysisRun.Status.ANALYZING, 40)

    # Technical — works without HTML (robots.txt, sitemap, llms.txt, HTTPS)
    technical_score_val, technical_details = score_technical(crawl)

    # Run entity + AI visibility + brand visibility in parallel
    entity_score_val, entity_details = 0.0, {}
    ai_vis_score, ai_vis_details, probes_data = 0.0, {}, []
    brand_vis_result = None

    brand_name = run.brand_name or extract_brand_name(run.url)
    if not run.brand_name and brand_name:
        run.brand_name = brand_name
        run.save(update_fields=["brand_name"])

    def _run_entity():
        return score_entity(crawl)

    def _run_ai_vis():
        return score_ai_visibility(crawl, target_country=(run.country or "").strip() or None)

    def _run_brand_vis():
        return run_brand_visibility(brand_name, run.url)

    _update_status(run, AnalysisRun.Status.ANALYZING, 55)

    with ThreadPoolExecutor(max_workers=3) as executor:
        entity_future = executor.submit(_run_entity)
        ai_vis_future = executor.submit(_run_ai_vis)
        brand_vis_future = executor.submit(_run_brand_vis)

        try:
            entity_score_val, entity_details = entity_future.result()
        except Exception as exc:
            logger.warning("Entity scoring failed for run %d: %s", run.id, exc)
            entity_details = {"error": str(exc)}

        try:
            ai_vis_score, ai_vis_details, probes_data = ai_vis_future.result()
        except Exception as exc:
            logger.warning("AI visibility failed for run %d: %s", run.id, exc)
            ai_vis_details = {"error": str(exc)}

        try:
            brand_vis_result = brand_vis_future.result()
        except Exception as exc:
            logger.warning("Brand visibility failed for run %d: %s", run.id, exc)

    _update_status(run, AnalysisRun.Status.ANALYZING, 80)

    _save_probes_and_tracks(run, probes_data, run.brand_name or run.url, run.url)

    _update_status(run, AnalysisRun.Status.SCORING, 85)

    composite = compute_composite(
        content_score, schema_score_val, eeat_score_val,
        technical_score_val, entity_score_val, ai_vis_score,
    )

    PageScore.objects.create(
        analysis_run=run,
        url=run.url,
        content_score=content_score,
        content_details=content_details,
        schema_score=schema_score_val,
        schema_details=schema_details,
        eeat_score=eeat_score_val,
        eeat_details=eeat_details,
        technical_score=technical_score_val,
        technical_details=technical_details,
        entity_score=entity_score_val,
        entity_details=entity_details,
        ai_visibility_score=ai_vis_score,
        ai_visibility_details=ai_vis_details,
        composite_score=composite,
    )

    # Recommendations
    pillar_details = {
        "content": content_details,
        "schema": schema_details,
        "eeat": eeat_details,
        "technical": technical_details,
        "entity": entity_details,
        "ai_visibility": ai_vis_details,
    }
    technical_details.setdefault("findings", [])
    if crawl.status_code == 403:
        technical_details["findings"].append("crawl_blocked_403")
    elif "timed out" in crawl.error.lower():
        technical_details["findings"].append("crawl_timeout")

    recs = generate_recommendations(pillar_details)
    for rec in recs:
        Recommendation.objects.create(analysis_run=run, **rec)

    # Save brand visibility
    if brand_vis_result:
        BrandVisibility.objects.create(analysis_run=run, **brand_vis_result)

    # Finalize as complete (partial), not failed
    run.composite_score = composite
    run.status = AnalysisRun.Status.COMPLETE
    run.progress = 100
    run.error_message = f"Partial results: {crawl.error}. Content, schema, and E-E-A-T could not be analyzed."
    run.llm_logs = get_collected_logs()
    run.save()
    logger.info("Partial analysis complete for run %d: score %.1f", run.id, composite)


def run_single_page_analysis(run_id: int):
    """Full analysis pipeline for a single page."""
    try:
        run = AnalysisRun.objects.get(pk=run_id)
    except AnalysisRun.DoesNotExist:
        logger.error("AnalysisRun %d not found", run_id)
        return

    try:
        start_log_collection()

        # Phase 1: Crawl (public URL first, then API fallback)
        _update_status(run, AnalysisRun.Status.CRAWLING, 5)
        crawl = crawl_page(run.url)

        if not crawl.ok:
            # Try fetching via connected integration (handles password-protected stores)
            api_crawl = _crawl_via_integration(run)
            if api_crawl and api_crawl.ok:
                crawl = api_crawl
            else:
                _run_partial_analysis(run, crawl)
                return

        _update_status(run, AnalysisRun.Status.ANALYZING, 15)

        # Detect industry for adaptive weights
        industry = detect_industry(crawl.soup, crawl.text)
        logger.info("Run %d: detected industry = %s", run_id, industry)

        # Phase 2: Run static pillars (fast, no LLM)
        content_score, content_details = score_content(crawl)
        schema_score_val, schema_details = score_schema(crawl)
        technical_score_val, technical_details = score_technical(crawl)
        _update_status(run, AnalysisRun.Status.ANALYZING, 30)

        # Derive brand name
        brand_name = run.brand_name or extract_brand_name(run.url)
        if not run.brand_name and brand_name:
            run.brand_name = brand_name
            run.save(update_fields=["brand_name"])

        # Phase 3: Run LLM-dependent pillars + brand visibility IN PARALLEL
        eeat_score_val, eeat_details = 0.0, {}
        entity_score_val, entity_details = 0.0, {}
        ai_vis_score, ai_vis_details, probes_data = 0.0, {}, []
        brand_vis_result = None

        def _run_eeat():
            return score_eeat(crawl)

        def _run_entity():
            return score_entity(crawl, industry=industry)

        def _run_ai_vis():
            return score_ai_visibility(crawl, target_country=(run.country or "").strip() or None)

        def _run_brand_vis():
            return run_brand_visibility(brand_name, run.url)

        with ThreadPoolExecutor(max_workers=4) as executor:
            eeat_future = executor.submit(_run_eeat)
            entity_future = executor.submit(_run_entity)
            ai_vis_future = executor.submit(_run_ai_vis)
            brand_vis_future = executor.submit(_run_brand_vis)

            try:
                eeat_score_val, eeat_details = eeat_future.result()
            except Exception as exc:
                logger.warning("E-E-A-T scoring failed for run %d: %s", run_id, exc)
                eeat_details = {"error": str(exc)}

            _update_status(run, AnalysisRun.Status.ANALYZING, 50)

            try:
                entity_score_val, entity_details = entity_future.result()
            except Exception as exc:
                logger.warning("Entity scoring failed for run %d: %s", run_id, exc)
                entity_details = {"error": str(exc)}

            _update_status(run, AnalysisRun.Status.ANALYZING, 65)

            try:
                ai_vis_score, ai_vis_details, probes_data = ai_vis_future.result()
            except Exception as exc:
                logger.warning("AI visibility failed for run %d: %s", run_id, exc)
                ai_vis_details = {"error": str(exc)}

            try:
                brand_vis_result = brand_vis_future.result()
            except Exception as exc:
                logger.warning("Brand visibility failed for run %d: %s", run_id, exc)

        _update_status(run, AnalysisRun.Status.ANALYZING, 75)

        # Save AI probes + backfill prompt tracking
        _save_probes_and_tracks(run, probes_data, run.brand_name or run.url, run.url)

        # Phase 4: Scoring
        _update_status(run, AnalysisRun.Status.SCORING, 80)

        composite = compute_composite(
            content_score, schema_score_val, eeat_score_val,
            technical_score_val, entity_score_val, ai_vis_score,
            industry=industry,
        )

        PageScore.objects.create(
            analysis_run=run,
            url=run.url,
            content_score=content_score,
            content_details=content_details,
            schema_score=schema_score_val,
            schema_details=schema_details,
            eeat_score=eeat_score_val,
            eeat_details=eeat_details,
            technical_score=technical_score_val,
            technical_details=technical_details,
            entity_score=entity_score_val,
            entity_details=entity_details,
            ai_visibility_score=ai_vis_score,
            ai_visibility_details=ai_vis_details,
            composite_score=composite,
        )

        # Phase 5: Recommendations
        pillar_details = {
            "content": content_details,
            "schema": schema_details,
            "eeat": eeat_details,
            "technical": technical_details,
            "entity": entity_details,
            "ai_visibility": ai_vis_details,
        }
        recs = generate_recommendations(pillar_details)
        for rec in recs:
            Recommendation.objects.create(analysis_run=run, **rec)

        # Save brand visibility
        if brand_vis_result:
            BrandVisibility.objects.create(analysis_run=run, **brand_vis_result)

        # Phase 6: Competitor discovery & scoring (static-only, no LLM for competitors)
        _update_status(run, AnalysisRun.Status.SCORING, 85)
        try:
            competitor_list = discover_competitors(crawl, user_country=(run.country or "").strip() or None)

            # Score competitors in parallel (static-only, no LLM)
            def _score_comp(comp_data):
                page_data, comp_composite = _score_competitor_static(comp_data["url"])
                return comp_data, page_data, comp_composite

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(_score_comp, cd) for cd in competitor_list]
                for future in as_completed(futures):
                    try:
                        comp_data, page_data, comp_composite = future.result()
                        comp = Competitor.objects.create(
                            analysis_run=run,
                            name=comp_data["name"],
                            url=comp_data["url"],
                            industry=comp_data.get("industry", ""),
                            tier=comp_data.get("tier", ""),
                            target_market=comp_data.get("target_market", ""),
                            geography=comp_data.get("geography", ""),
                            pricing_model=comp_data.get("pricing_model", ""),
                            estimated_revenue_band=comp_data.get("estimated_revenue_band", ""),
                            positioning=comp_data.get("positioning", ""),
                            relevance_score=comp_data.get("relevance_score"),
                        )
                        if page_data:
                            comp_page = PageScore.objects.create(
                                analysis_run=run, **page_data
                            )
                            comp.page_score = comp_page
                            comp.composite_score = comp_composite
                            comp.scored = True
                            comp.save()
                    except Exception as exc:
                        logger.warning("Competitor scoring failed: %s", exc)
        except Exception as exc:
            logger.warning("Competitor discovery failed for run %d: %s", run_id, exc)

        # Finalize
        run.composite_score = composite
        run.status = AnalysisRun.Status.COMPLETE
        run.progress = 100
        run.llm_logs = get_collected_logs()
        run.save()
        logger.info("Analysis complete for run %d: score %.1f", run_id, composite)

    except Exception as exc:
        logger.error("Analysis failed for run %d: %s", run_id, exc, exc_info=True)
        run.status = AnalysisRun.Status.FAILED
        run.error_message = str(exc)
        run.save()


def start_analysis_task(run_id: int):
    """Start the analysis in a background thread."""
    try:
        run = AnalysisRun.objects.get(pk=run_id)
    except AnalysisRun.DoesNotExist:
        return

    thread = threading.Thread(target=run_single_page_analysis, args=(run_id,), daemon=True)
    thread.start()
