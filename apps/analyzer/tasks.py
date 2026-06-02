import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from .models import (
    AIVisibilityProbe,
    AnalysisRun,
    BrandVisibility,
    Competitor,
    PageScore,
    PromptTrack,
    Recommendation,
)
from .pipeline.aggregator import compute_composite, compute_static_composite, detect_industry
from .pipeline.ai_visibility import score_ai_visibility
from .pipeline.brand_naming import visibility_brand_label
from .pipeline.brand_visibility import run_brand_visibility
from .pipeline.competitors import discover_competitors
from .pipeline.content import score_content
from .pipeline.crawler import CrawlResult, crawl_page
from .pipeline.eeat import score_eeat
from .pipeline.entity import score_entity
from .pipeline.llm import get_collected_logs, start_log_collection
from .pipeline.recommendations import generate_recommendations
from .pipeline.schema import score_schema
from .pipeline.technical import score_technical

logger = logging.getLogger("apps")


def _crawl_via_integration(run: AnalysisRun) -> CrawlResult | None:
    """Fallback: fetch page content via Shopify/WordPress API when public crawl fails."""
    from bs4 import BeautifulSoup

    from apps.integrations.models import Integration

    from .pipeline.utils import extract_internal_links, extract_text

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
        from .auto_fix import _read_page_content

        html_content = _read_page_content(integration, run.url)

        if not html_content:
            return None

        # Reject content too short to analyze meaningfully
        if len(html_content.strip()) < 50:
            logger.warning(
                "Run %d: integration content too short (%d chars), skipping", run.id, len(html_content)
            )
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
        logger.info(
            "Run %d: crawled via %s integration (API fallback, %d chars, %d text chars)",
            run.id,
            integration.provider,
            len(html_content),
            len(text),
        )
        return result
    except Exception as e:
        logger.warning("Run %d: integration crawl fallback failed: %s", run.id, e)
        return None


def _save_probes_and_tracks(
    run: AnalysisRun,
    probes_data: list[dict],
    brand_name: str,
    brand_url: str,
    crawl_text: str = "",
    meta_description: str = "",
    site_pages: list[str] | None = None,
    industry: str = "",
    country: str = "",
):
    """Save AIVisibilityProbe rows and generate AI-powered brand-specific prompt tracks."""
    from apps.accounts.subscription_utils import get_plan_limits, is_plan_limits_enforcement_enabled

    from .pipeline.citations import competitor_hosts_for_run, host_of, persist_prompt_result
    from .pipeline.prompt_tracker import (
        classify_prompt_intent_and_type,
        compute_prompt_score,
        fire_prompt_across_engines,
        generate_brand_prompts,
    )

    # Save visibility probes
    for probe in probes_data:
        AIVisibilityProbe.objects.create(analysis_run=run, **probe)

    em = (run.email or "").strip().lower()
    limits = get_plan_limits(run.email)
    allowed_engines = limits["engines"] if is_plan_limits_enforcement_enabled() and em else None
    if is_plan_limits_enforcement_enabled() and em:
        cur = PromptTrack.objects.filter(analysis_run__email=em).count()
        slots = max(0, limits["max_prompts"] - cur)
        gen_count = min(10, slots)
    else:
        gen_count = 10

    stored = list(run.onboarding_prompts or []) if getattr(run, "onboarding_prompts", None) else []
    stored = [p.strip() for p in stored if isinstance(p, str) and p.strip()]

    if gen_count == 0:
        brand_prompts = []
    elif stored:
        brand_prompts = stored[:gen_count]
    else:
        try:
            brand_prompts = generate_brand_prompts(
                brand_name=brand_name,
                brand_url=brand_url,
                industry=industry,
                page_content=crawl_text,
                meta_description=meta_description,
                products=site_pages,
                location="",
                country=country,
                count=gen_count,
            )
        except Exception as exc:
            logger.warning("AI prompt generation failed for run %d: %s", run.id, exc)
            brand_prompts = []
        brand_prompts = brand_prompts[:gen_count]

    # Fire all prompts in parallel — each prompt hits 4 LLMs + Google + Bing
    # (independent of every other prompt), so a thread pool collapses what was
    # ~10 × per-prompt-latency down to roughly one prompt's worth of wall time.
    # max_workers=5 throttles concurrent provider load while still getting
    # most of the speedup; each worker internally fans out to all engines.
    brand_host = host_of(brand_url)
    rival_hosts = competitor_hosts_for_run(run)

    def _process_prompt(prompt_text: str):
        intent, prompt_type = classify_prompt_intent_and_type(
            prompt_text,
            brand_name,
            brand_url,
        )
        engine_results = fire_prompt_across_engines(
            prompt_text,
            brand_name,
            brand_url,
            runs=1,
            allowed_engines=allowed_engines,
        )
        return prompt_text, intent, prompt_type, engine_results

    processed: list[tuple[str, str, str, list[dict]]] = []
    if brand_prompts:
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_process_prompt, p) for p in brand_prompts]
            for future in as_completed(futures):
                try:
                    processed.append(future.result())
                except Exception as exc:
                    logger.warning("Prompt processing failed for run %d: %s", run.id, exc)

    # DB writes stay sequential — Django ORM isn't thread-safe across saves
    # on SQLite, and the writes themselves are fast (no LLM latency).
    for prompt_text, intent, prompt_type, engine_results in processed:
        try:
            track = PromptTrack.objects.create(
                analysis_run=run,
                prompt_text=prompt_text,
                is_custom=False,
                intent=intent,
                prompt_type=prompt_type,
            )
            for r in engine_results:
                persist_prompt_result(track, r, brand_host, rival_hosts)

            all_results = list(
                track.results.values(
                    "brand_mentioned",
                    "sentiment",
                    "rank_position",
                    "confidence",
                    "engine",
                )
            )
            score_data = compute_prompt_score(all_results)
            track.score = score_data["score"]
            track.authority_score = score_data["authority_score"]
            track.content_quality_score = score_data["content_quality_score"]
            track.structural_score = score_data["structural_score"]
            track.semantic_score = score_data["semantic_score"]
            track.third_party_score = score_data["third_party_score"]
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
        except Exception as exc:
            logger.warning("PromptTrack persist failed for run %d: %s", run.id, exc)


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
    content_score, content_details = (
        0.0,
        {
            "checks": {"crawl_failed": True},
            "findings": [],
            "note": f"Page content could not be accessed: {crawl.error}",
        },
    )
    schema_score_val, schema_details = (
        0.0,
        {
            "checks": {"crawl_failed": True},
            "findings": [],
            "note": f"Schema markup could not be checked: {crawl.error}",
        },
    )
    eeat_score_val, eeat_details = (
        0.0,
        {
            "checks": {"crawl_failed": True},
            "findings": [],
            "note": f"E-E-A-T signals could not be analyzed: {crawl.error}",
        },
    )

    _update_status(run, AnalysisRun.Status.ANALYZING, 40)

    # Technical — works without HTML (robots.txt, sitemap, llms.txt, HTTPS)
    technical_score_val, technical_details = score_technical(crawl)

    # Run entity + AI visibility + brand visibility in parallel
    entity_score_val, entity_details = 0.0, {}
    ai_vis_score, ai_vis_details, probes_data = 0.0, {}, []
    brand_vis_result = None

    brand_name = visibility_brand_label(run.url, run.brand_name)
    if run.brand_name != brand_name:
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

    _save_probes_and_tracks(
        run,
        probes_data,
        run.brand_name or run.url,
        run.url,
        crawl_text=crawl.text[:2000] if crawl.text else "",
    )

    _update_status(run, AnalysisRun.Status.SCORING, 85)

    composite = compute_composite(
        content_score,
        schema_score_val,
        eeat_score_val,
        technical_score_val,
        entity_score_val,
        ai_vis_score,
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

        # Check if store has a storefront password (Shopify dev stores)
        storefront_password = ""
        if run.organization:
            from apps.integrations.models import Integration

            try:
                integration = Integration.objects.filter(
                    organization=run.organization,
                    is_active=True,
                    provider__in=["shopify", "wordpress"],
                ).first()
                if integration:
                    storefront_password = integration.metadata.get("storefront_password", "")
            except Exception:
                pass

        if not storefront_password:
            storefront_password = run.storefront_password or ""

        from .pipeline.crawler import crawl_site

        homepage_crawl, site_map, additional_crawls = crawl_site(
            run.url,
            storefront_password=storefront_password,
            max_pages=12,
        )
        crawl = homepage_crawl  # Primary crawl for backward compatibility

        if not crawl.ok:
            # Try fetching via connected integration (handles password-protected stores)
            api_crawl = _crawl_via_integration(run)
            if api_crawl and api_crawl.ok:
                crawl = api_crawl
            else:
                # Check if this is a hard failure (no point in partial analysis)
                err = crawl.error or ""
                is_hard_fail = any(
                    kw in err.lower()
                    for kw in [
                        "password-protected",
                        "domain not found",
                        "ssl certificate",
                        "connection refused",
                        "not found (404)",
                        "permanently removed",
                    ]
                )
                if is_hard_fail:
                    run.status = AnalysisRun.Status.FAILED
                    run.error_message = crawl.error
                    run.save(update_fields=["status", "error_message"])
                    logger.warning("Run %d hard failed: %s", run.id, crawl.error)
                    return
                # Soft failure — run partial analysis
                _run_partial_analysis(run, crawl)
                return

        _update_status(run, AnalysisRun.Status.ANALYZING, 15)

        # Content hashing for change detection
        import hashlib

        content_hash = hashlib.sha256((crawl.text or "").encode()).hexdigest()
        run.content_hash = content_hash
        run.save(update_fields=["content_hash"])

        # Check if content changed since last run
        prev_run = (
            AnalysisRun.objects.filter(url=run.url, status="complete")
            .exclude(pk=run.pk)
            .order_by("-created_at")
            .first()
        )

        if prev_run and prev_run.content_hash == content_hash:
            # Content unchanged — reuse previous scores for static pillars
            prev_page = prev_run.page_scores.filter(url=run.url).first()
            if prev_page:
                logger.info(
                    "Run %d: content unchanged (hash=%s), reusing static scores from run %d",
                    run_id,
                    content_hash[:12],
                    prev_run.pk,
                )

        # Detect industry for adaptive weights
        industry = detect_industry(crawl.soup, crawl.text)
        logger.info("Run %d: detected industry = %s", run_id, industry)

        # Phase 2: Run static pillars across ALL crawled pages
        # Score homepage first
        content_score, content_details = score_content(crawl)
        schema_score_val, schema_details = score_schema(crawl)
        technical_score_val, technical_details = score_technical(crawl)

        # Score additional pages and aggregate
        all_content_scores = [content_score]
        all_schema_scores = [schema_score_val]
        page_scores_data = []

        for extra_crawl in additional_crawls:
            if not extra_crawl.ok:
                continue
            try:
                c_score, c_details = score_content(extra_crawl)
                s_score, s_details = score_schema(extra_crawl)
                all_content_scores.append(c_score)
                all_schema_scores.append(s_score)
                page_scores_data.append(
                    {
                        "url": extra_crawl.url,
                        "content_score": c_score,
                        "schema_score": s_score,
                        "content_details": c_details,
                        "schema_details": s_details,
                    }
                )
            except Exception as exc:
                logger.warning("Scoring failed for %s: %s", extra_crawl.url, exc)

        # Aggregate: use weighted average (homepage 40%, rest split 60%)
        if len(all_content_scores) > 1:
            other_content_avg = sum(all_content_scores[1:]) / len(all_content_scores[1:])
            content_score = all_content_scores[0] * 0.4 + other_content_avg * 0.6
            content_details["site_pages_scored"] = len(all_content_scores)
            content_details["homepage_score"] = all_content_scores[0]
            content_details["pages_avg_score"] = round(other_content_avg, 1)

        if len(all_schema_scores) > 1:
            other_schema_avg = sum(all_schema_scores[1:]) / len(all_schema_scores[1:])
            schema_score_val = all_schema_scores[0] * 0.4 + other_schema_avg * 0.6
            schema_details["site_pages_scored"] = len(all_schema_scores)

        # Store discovery info
        content_details["site_discovery"] = {
            "products": len(site_map.products),
            "collections": len(site_map.collections),
            "pages": len(site_map.pages),
            "blog_posts": len(site_map.blog_posts),
            "total_discovered": site_map.total,
            "pages_crawled": 1 + len(additional_crawls),
        }

        _update_status(run, AnalysisRun.Status.ANALYZING, 30)

        # Derive brand label from URL (corrects generic / mismatched stored names)
        brand_name = visibility_brand_label(run.url, run.brand_name)
        if run.brand_name != brand_name:
            run.brand_name = brand_name
            run.save(update_fields=["brand_name"])

        # Phase 3: Run LLM-dependent pillars + brand visibility IN PARALLEL
        eeat_score_val, eeat_details = 0.0, {}
        entity_score_val, entity_details = 0.0, {}
        ai_vis_score, ai_vis_details, probes_data = 0.0, {}, []
        brand_vis_result = None

        def _run_eeat():
            # Score E-E-A-T on homepage + aggregate with additional pages
            main_score, main_details = score_eeat(crawl)
            if additional_crawls:
                extra_scores = []
                for ec in additional_crawls:
                    if ec.ok:
                        try:
                            es, _ = score_eeat(ec, skip_gemini=True)
                            extra_scores.append(es)
                        except Exception:
                            pass
                if extra_scores:
                    extra_avg = sum(extra_scores) / len(extra_scores)
                    main_score = main_score * 0.4 + extra_avg * 0.6
                    main_details["site_pages_scored"] = 1 + len(extra_scores)
            return main_score, main_details

        def _run_entity():
            return score_entity(crawl, industry=industry, override_brand=brand_name)

        def _run_ai_vis():
            return score_ai_visibility(
                crawl, target_country=(run.country or "").strip() or None, override_brand=brand_name
            )

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

        # Save AI probes + backfill prompt tracking with full brand context
        # Extract meta description for prompt generation
        _meta_desc = ""
        if crawl.soup:
            _md = crawl.soup.find("meta", attrs={"name": "description"})
            _meta_desc = _md["content"].strip() if _md and _md.get("content") else ""

        # Get page titles from discovered site pages
        _site_page_titles = []
        for ec in additional_crawls:
            if ec.ok and ec.soup:
                t = ec.soup.find("title")
                if t and t.get_text(strip=True):
                    _site_page_titles.append(t.get_text(strip=True))

        _save_probes_and_tracks(
            run,
            probes_data,
            run.brand_name or run.url,
            run.url,
            crawl_text=crawl.text[:2000],
            meta_description=_meta_desc,
            site_pages=_site_page_titles or None,
            industry=industry,
            country=(run.country or "").strip(),
        )

        # Phase 4: Scoring with smoothing
        _update_status(run, AnalysisRun.Status.SCORING, 80)

        # Score smoothing: blend LLM-dependent pillars with previous run
        # Static pillars (content, schema, technical) are NOT smoothed — they reflect current state
        # LLM pillars (eeat, entity, ai_visibility) are smoothed to reduce noise
        prev_page = (
            PageScore.objects.filter(url=run.url, analysis_run__status="complete")
            .exclude(analysis_run=run)
            .order_by("-created_at")
            .first()
        )

        SMOOTH_ALPHA = 0.4  # weight for NEW score
        if prev_page:
            raw_eeat = eeat_score_val
            raw_entity = entity_score_val
            raw_ai_vis = ai_vis_score

            eeat_score_val = prev_page.eeat_score * (1 - SMOOTH_ALPHA) + eeat_score_val * SMOOTH_ALPHA
            entity_score_val = prev_page.entity_score * (1 - SMOOTH_ALPHA) + entity_score_val * SMOOTH_ALPHA
            ai_vis_score = prev_page.ai_visibility_score * (1 - SMOOTH_ALPHA) + ai_vis_score * SMOOTH_ALPHA

            logger.info(
                "Run %d: smoothed scores - E-E-A-T: %.1f->%.1f, Entity: %.1f->%.1f, AI Vis: %.1f->%.1f",
                run_id,
                raw_eeat,
                eeat_score_val,
                raw_entity,
                entity_score_val,
                raw_ai_vis,
                ai_vis_score,
            )

            # Store raw scores for transparency
            eeat_details["raw_score"] = raw_eeat
            eeat_details["smoothed_from_run"] = prev_page.analysis_run_id
            entity_details["raw_score"] = raw_entity
            ai_vis_details["raw_score"] = raw_ai_vis

        composite = compute_composite(
            content_score,
            schema_score_val,
            eeat_score_val,
            technical_score_val,
            entity_score_val,
            ai_vis_score,
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
            content_hash=content_hash,
        )

        # Save per-page scores for additional crawled pages
        for pd in page_scores_data:
            try:
                PageScore.objects.create(
                    analysis_run=run,
                    url=pd["url"],
                    content_score=pd["content_score"],
                    content_details=pd["content_details"],
                    schema_score=pd["schema_score"],
                    schema_details=pd["schema_details"],
                    eeat_score=0,
                    eeat_details={},
                    technical_score=0,
                    technical_details={},
                    entity_score=0,
                    entity_details={},
                    ai_visibility_score=0,
                    ai_visibility_details={},
                    composite_score=0,
                )
            except Exception:
                pass

        # Phase 5: Recommendations
        pillar_details = {
            "content": content_details,
            "schema": schema_details,
            "eeat": eeat_details,
            "technical": technical_details,
            "entity": entity_details,
            "ai_visibility": ai_vis_details,
        }
        pillar_scores = {
            "content": content_score,
            "schema": schema_score_val,
            "eeat": eeat_score_val,
            "technical": technical_score_val,
            "entity": entity_score_val,
            "ai_visibility": ai_vis_score,
        }
        recs = generate_recommendations(pillar_details, pillar_scores=pillar_scores)
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
                            comp_page = PageScore.objects.create(analysis_run=run, **page_data)
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
        _generate_and_fire_competitive_prompts(run)

    except Exception as exc:
        logger.error("Analysis failed for run %d: %s", run_id, exc, exc_info=True)
        run.status = AnalysisRun.Status.FAILED
        run.error_message = str(exc)
        run.save()


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
    from .pipeline.prompt_tracker import (
        fire_prompt_across_engines,
        compute_prompt_score,
    )
    from .pipeline.citations import (
        persist_prompt_result,
        host_of,
        competitor_hosts_for_run,
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

        all_res = list(track.results.values(
            "brand_mentioned", "sentiment", "rank_position", "confidence", "engine"
        ))
        sd = compute_prompt_score(all_res)
        track.score = sd["score"]
        track.authority_score = sd["authority_score"]
        track.content_quality_score = sd["content_quality_score"]
        track.structural_score = sd["structural_score"]
        track.semantic_score = sd["semantic_score"]
        track.third_party_score = sd["third_party_score"]
        track.save(update_fields=[
            "score", "authority_score", "content_quality_score",
            "structural_score", "semantic_score", "third_party_score",
        ])
    except Exception:
        logger.exception(
            "Fast competitive fire failed for track %d (run %d)",
            track.id, track.analysis_run_id,
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
                existing_competitive, run.id,
            )
            return

        from .pipeline.prompt_tracker import (
            generate_brand_prompts,
            classify_prompt_intent_and_type,
        )

        brand = (run.brand_name or "").strip() or _domain_label(run.url)
        url = (run.url or "").strip()

        prompts = generate_brand_prompts(
            brand_name=brand,
            brand_url=url,
            country=(run.country or "").strip(),
            count=10,
        )
        if not prompts:
            logger.info("Auto-gen returned no competitive prompts for run %d.", run.id)
            return

        # Persist tracks first so the Prompts page renders the queue immediately
        # while engine fires populate results asynchronously.
        tracks: list[PromptTrack] = []
        for prompt_text in prompts:
            intent, _ = classify_prompt_intent_and_type(prompt_text, brand, url)
            tracks.append(PromptTrack.objects.create(
                analysis_run=run,
                prompt_text=prompt_text,
                is_custom=False,
                intent=intent,
                prompt_type=PromptTrack.PromptSurfaceType.COMPETITIVE,
            ))

        def _fire_all():
            # Parallel pool: 4 prompts in flight, each one fires its engines
            # in parallel internally. Keeps total wall-clock to ~3x slowest
            # engine round-trip instead of 10x.
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [
                    pool.submit(_fire_competitive_prompt_fast, t, brand, url)
                    for t in tracks
                ]
                for fut in as_completed(futures):
                    try:
                        fut.result()
                    except Exception:
                        # _fire_competitive_prompt_fast already logs internally.
                        pass

        threading.Thread(target=_fire_all, daemon=True).start()
        logger.info(
            "Queued %d competitive prompts for run %d (runs=1, pool=4).",
            len(tracks), run.id,
        )

    except Exception:
        logger.exception("Auto-competitive prompt generation failed for run %d", run.id)


def _kickoff_sitemap_audit(run_id: int) -> None:
    """Create a SitemapAudit row and dispatch the crawl to a Celery worker.

    Called from start_analysis_task so the sitemap crawl runs in PARALLEL
    with the main page analysis — the Sitemap tab shows progress immediately
    instead of staying on the "Run audit" empty state until the main run
    finishes. Failures here are non-fatal: the main analysis is independent.

    When CELERY_BROKER_URL is unset (local dev), the task runs eagerly
    in-process via CELERY_TASK_ALWAYS_EAGER.
    """
    try:
        from .celery_tasks import run_sitemap_audit_task
        from .models import SitemapAudit
        from .pipeline.sitemap_audit import HARD_URL_CAP

        run = AnalysisRun.objects.get(pk=run_id)
        audit = SitemapAudit.objects.create(
            analysis_run=run,
            status=SitemapAudit.Status.QUEUED,
            crawl_limit=HARD_URL_CAP,
        )
        run_sitemap_audit_task.delay(audit.id)
    except Exception as exc:
        logger.warning("Auto sitemap audit kickoff failed for run %d: %s", run_id, exc)


def start_analysis_task(run_id: int):
    """Start the main analysis in a background thread and fire the sitemap
    audit in parallel so the Sitemap tab is populated as soon as possible."""
    if not AnalysisRun.objects.filter(pk=run_id).exists():
        return

    thread = threading.Thread(target=run_single_page_analysis, args=(run_id,), daemon=True)
    thread.start()

    # Sibling sitemap audit — runs concurrently with the main analysis above.
    _kickoff_sitemap_audit(run_id)
