"""Blog automation: draft generation, scheduling and publishing.

Moved out of views/_shared.py, which was itself carved out of the original
8,409-line views.py. This is business logic, not view code (ARCHITECTURE.md
§2: services/ holds the rules, views parse and delegate).

Still re-exported through ``apps.analyzer.views`` for one release:
``backlink_engine`` and several tests resolve these as attributes of that
module, including via ``mock.patch``.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse

from django.db import DatabaseError
from django.utils import timezone

from apps.integrations.models import Integration
from apps.organizations.models import Organization

from ..models import (
    AnalysisRun,
    BlogAutomationConfig,
    BlogAutomationJob,
    PromptTrack,
)
from .site_resolution import (  # noqa: F401
    _normalize_origin,
    _resolve_crawl_site,
    _safe_first,
)

logger = logging.getLogger("apps")

BLOG_MODEL_PROVIDER = os.getenv("SIGNALOR_BLOG_MODEL", "opus").strip() or "opus"

def _slugify(text: str) -> str:
    value = "".join(ch.lower() if ch.isalnum() else "-" for ch in (text or "").strip())
    while "--" in value:
        value = value.replace("--", "-")
    return value.strip("-")[:90] or "ai-visibility-guide"

def _extract_blog_json(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        snippet = raw[start : end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            return None
    return None

def _resolve_blog_integration(email: str):
    org = _safe_first(
        Organization.objects.filter(owner_email=email),
        context="blog org lookup",
    )
    if not org:
        return None, "none"

    wp = _safe_first(
        Integration.objects.filter(
            organization=org,
            provider=Integration.Provider.WORDPRESS,
            is_active=True,
        ),
        context="blog wordpress lookup",
    )
    if wp:
        return wp, "wordpress"

    shopify = _safe_first(
        Integration.objects.filter(
            organization=org,
            provider=Integration.Provider.SHOPIFY,
            is_active=True,
        ),
        context="blog shopify lookup",
    )
    if shopify:
        return shopify, "shopify"

    return None, "none"

def _short_title(text: str, max_words: int = 16) -> str:
    """Clean + clamp a blog title to a short headline (default ≤16 words).

    Strips trailing "Guide for <domain>" / "for <domain>" boilerplate and any
    leading lowercase angle phrasing, then trims to max_words.
    """
    import re as _re

    t = " ".join(str(text or "").split())
    # Drop trailing "... Guide for example.com" / "... for example.com"
    t = _re.sub(r"\s+(?:guide\s+)?for\s+[\w.-]+\.[a-z]{2,}\s*$", "", t, flags=_re.I)
    t = t.strip(" -–—:") or "Untitled"
    words = t.split()
    if len(words) > max_words:
        t = " ".join(words[:max_words]).rstrip(" ,.;:") + "…"
    return t[0].upper() + t[1:] if t else t

def _meter_blog_spend(run, spend: dict, what: str) -> None:
    """Fold blog spend into the account's 30-day budget window.

    ``check_budget`` sums ``AnalysisRun.llm_cost_usd``. Blog generation happens
    outside any run's log window, so without this it was invisible to the fuse:
    an account could generate unlimited Opus posts and never trip its cap.
    """
    cost = float((spend or {}).get("cost", 0.0) or 0.0)
    if cost <= 0 or run is None:
        return
    try:
        from django.db.models import F

        AnalysisRun.objects.filter(pk=run.pk).update(llm_cost_usd=F("llm_cost_usd") + cost)
        logger.info(
            "Blog %s for run %s: +$%.4f over %d call(s) on %s",
            what, run.pk, cost, (spend or {}).get("calls", 0), BLOG_MODEL_PROVIDER,
        )
    except Exception:
        logger.exception("Could not record blog spend for run %s", getattr(run, "pk", "?"))

def _generate_blog_draft(
    site_url: str,
    topic: str,
    keywords: list[str],
    recommendations: list[str],
    length: str = "medium",
    sources: list | None = None,
    run=None,
) -> dict:
    from core.llm.client import ask_llm, cost_scope

    word_target, min_words, max_tokens = {
        "short": ("about 500 words", 450, 1400),
        "medium": ("1000-1500 words", 1000, 3200),
        "long": ("1500-3000 words", 1800, 6000),
    }.get((length or "medium").lower(), ("1000-1500 words", 1000, 3200))

    src_lines = []
    for s in sources or []:
        if isinstance(s, dict):
            name = (s.get("name") or "").strip()
            url = (s.get("url") or "").strip()
            label = f"{name} ({url})" if url else name
        else:
            label = str(s).strip()
        if label:
            src_lines.append(label)
    sources_block = (
        "\nTake reference and inspiration from these sources (cite or link a couple "
        "where it reads naturally):\n" + "\n".join(f"- {x}" for x in src_lines)
        if src_lines
        else ""
    )

    prompt = f"""
You are an expert SEO + GEO content strategist.
Generate a long-form blog post draft for this website:

Site URL: {site_url}
Primary Topic: {topic}
Target Keywords: {", ".join(keywords) if keywords else "none"}
Technical recommendations to align with:
{chr(10).join(f"- {item}" for item in recommendations[:6]) if recommendations else "- Improve AI visibility and crawlability"}
{sources_block}

Return STRICT JSON only with keys:
title, slug, meta_description, excerpt, content_markdown, tags

Requirements:
- title: a punchy headline, MAX 12 words. Do NOT restate the prompt verbatim and
  do NOT append the site name or "Guide for ...".
- slug: URL-safe
- meta_description: max 160 chars
- excerpt: 2-3 sentences
- meta_description: ALWAYS write a 140-160 char SEO summary (never leave blank)
- content_markdown: write {word_target} — HARD requirement: the body MUST be at
  least {min_words} words. Do NOT stop early or summarize; use multiple H2/H3
  sections with examples and actionable detail to reach the length.
- content_markdown MUST include 1-2 natural, contextual links back to the brand
  site ({site_url}) using markdown link syntax [anchor]({site_url}), placed where
  they read naturally — these are the backlinks.
- tags: array of 4-8 short tags
- Mention practical steps readers can apply.
"""

    with cost_scope() as spend:
        raw = ask_llm(
            prompt=prompt.strip(),
            preferred_provider=BLOG_MODEL_PROVIDER,
            max_tokens=max_tokens,
            temperature=0.5,
            purpose="actions.blog_automation.generate",
        )
    _meter_blog_spend(run, spend, "generate")

    parsed = _extract_blog_json(raw) or {}
    title = _short_title(parsed.get("title") or topic or urlparse(site_url).netloc)
    slug = _slugify(str(parsed.get("slug") or title))
    meta_description = str(parsed.get("meta_description") or "")[:160].strip()
    excerpt = str(parsed.get("excerpt") or "").strip()
    content_markdown = str(parsed.get("content_markdown") or "").strip()

    if not content_markdown:
        content_markdown = (
            f"# {title}\n\n"
            f"{excerpt or 'This guide explains practical actions to improve your AI search visibility.'}\n\n"
            "## Why this matters\n"
            "AI-first search experiences reward brands that publish clear, structured, and credible content.\n\n"
            "## Core strategy\n"
            f"- Focus topic cluster: {topic}\n"
            f"- Target keywords: {', '.join(keywords) if keywords else 'n/a'}\n"
            "- Improve crawl files (llms.txt, robots.txt, sitemap.xml)\n\n"
            "## Execution checklist\n"
            "- Publish consistent educational posts\n"
            "- Add internal links to key pages\n"
            "- Track rankings and iterate monthly\n"
        )

    tags = parsed.get("tags")
    if not isinstance(tags, list):
        tags = [k for k in keywords[:6]]
    tags = [str(t).strip() for t in tags if str(t).strip()]

    return {
        "title": title,
        "slug": slug,
        "meta_description": meta_description,
        "excerpt": excerpt,
        "content_markdown": content_markdown,
        "tags": tags,
        "llm_raw": raw[:1500] if raw else "",
    }

def _parse_publish_time(raw: str | None):
    from datetime import time

    if not raw:
        return time(hour=9, minute=0)
    text = str(raw).strip()
    try:
        if len(text) == 5:
            return datetime.strptime(text, "%H:%M").time()
        return datetime.strptime(text[:8], "%H:%M:%S").time()
    except ValueError:
        return time(hour=9, minute=0)

def _to_html_from_markdownish(text: str) -> str:
    """Convert lightweight markdown (headings, bullet lists, bold, links) to HTML.

    Markdown links ``[text](url)`` become anchors — that's how the brand backlink
    is rendered on the satellite sites.
    """
    import html as _html
    import re as _re

    raw = (text or "").strip()
    if not raw:
        return "<p></p>"

    def inline(s: str) -> str:
        s = _html.escape(s, quote=False)
        s = _re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)
        s = _re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        return s

    out: list[str] = []
    list_buf: list[str] = []
    para_buf: list[str] = []

    def flush_list():
        if list_buf:
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in list_buf) + "</ul>")
            list_buf.clear()

    def flush_para():
        if para_buf:
            out.append(f"<p>{inline(' '.join(para_buf))}</p>")
            para_buf.clear()

    for line in raw.split("\n"):
        stripped = line.strip()
        if not stripped:
            flush_para()
            flush_list()
            continue
        heading = _re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_para()
            flush_list()
            level = min(len(heading.group(1)), 6)
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            continue
        if _re.match(r"^[-*]\s+", stripped):
            flush_para()
            list_buf.append(_re.sub(r"^[-*]\s+", "", stripped))
            continue
        flush_list()
        para_buf.append(stripped)

    flush_para()
    flush_list()
    return "".join(out) or "<p></p>"

def _get_or_create_blog_config(
    email: str,
    run_id: int | None,
    analyzed_url: str,
    topic: str = "",
    keywords: list[str] | None = None,
    mode: str | None = None,
    frequency_per_day: int | None = None,
    publish_time_raw: str | None = None,
    is_active: bool | None = None,
):
    site_url, _ = _resolve_crawl_site(email, run_id, analyzed_url)
    if not site_url:
        return None

    integration, provider = _resolve_blog_integration(email)
    org = _safe_first(
        Organization.objects.filter(owner_email=email),
        context="blog config org lookup",
    )
    run = (
        _safe_first(AnalysisRun.objects.filter(pk=run_id), context="blog config run lookup")
        if run_id
        else None
    )

    config = _safe_first(
        BlogAutomationConfig.objects.filter(user_email=email, site_url=site_url),
        context="blog config lookup",
    )
    if not config:
        config = BlogAutomationConfig(
            user_email=email,
            organization=org,
            analysis_run=run,
            site_url=site_url,
        )

    if topic.strip():
        config.topic = topic.strip()
    if keywords is not None:
        config.keywords = keywords
    if mode in {
        BlogAutomationConfig.PublishMode.AUTO_PUBLISH,
        BlogAutomationConfig.PublishMode.REVIEW_BEFORE_PUBLISH,
    }:
        config.mode = mode
    if frequency_per_day is not None:
        config.frequency_per_day = max(1, min(4, int(frequency_per_day)))
    if publish_time_raw is not None:
        config.publish_time = _parse_publish_time(publish_time_raw)
    if is_active is not None:
        config.is_active = bool(is_active)
    if provider in {
        BlogAutomationConfig.PublishProvider.WORDPRESS,
        BlogAutomationConfig.PublishProvider.SHOPIFY,
    }:
        config.publish_provider = provider
    else:
        config.publish_provider = BlogAutomationConfig.PublishProvider.NONE

    try:
        config.save()
    except DatabaseError:
        logger.exception("Failed saving blog automation config.")
        return None
    return config

def _enqueue_daily_jobs(config: BlogAutomationConfig, days_ahead: int = 21):
    start_day = timezone.localdate()
    end_day = start_day + timedelta(days=days_ahead)
    freq = max(1, min(4, int(config.frequency_per_day or 1)))
    interval_hours = 24 / freq
    tz = timezone.get_current_timezone()
    created = 0

    current = start_day
    while current <= end_day:
        for slot in range(freq):
            naive_dt = datetime.combine(current, config.publish_time) + timedelta(hours=slot * interval_hours)
            scheduled_for = timezone.make_aware(naive_dt, tz) if timezone.is_naive(naive_dt) else naive_dt

            try:
                _, was_created = BlogAutomationJob.objects.get_or_create(
                    config=config,
                    scheduled_for=scheduled_for,
                    defaults={
                        "user_email": config.user_email,
                        "analysis_run": config.analysis_run,
                        "provider": config.publish_provider,
                        "mode": config.mode,
                        "status": BlogAutomationJob.Status.SCHEDULED,
                        "topic": config.topic,
                        "keywords": config.keywords,
                    },
                )
            except DatabaseError:
                logger.exception("Failed creating queued blog automation job.")
                continue
            if was_created:
                created += 1
        current += timedelta(days=1)

    config.last_queued_for = end_day
    try:
        config.save(update_fields=["last_queued_for", "updated_at"])
    except DatabaseError:
        logger.warning("Failed updating blog config queue marker.")
    return created

def _publish_blog_job(job: BlogAutomationJob, publish_now: bool = True) -> dict:
    integration, provider = _resolve_blog_integration(job.user_email)
    if not integration or provider == "none":
        raise ValueError("No active WordPress/Shopify integration found for publishing.")

    if provider == "wordpress":
        from apps.integrations.services.wordpress import publish_wordpress_post

        published = publish_wordpress_post(
            integration=integration,
            title=job.title,
            content=job.content_markdown,
            excerpt=job.excerpt,
            status="publish" if publish_now else "draft",
            slug=job.slug,
        )
    else:
        from apps.integrations.services.shopify import create_shopify_blog_article

        published = create_shopify_blog_article(
            integration=integration,
            title=job.title,
            content_html=_to_html_from_markdownish(job.content_markdown),
            summary_html=job.excerpt,
            publish=publish_now,
            tags=[str(t).strip() for t in (job.tags or []) if str(t).strip()],
        )

    job.provider = provider
    job.external_post_id = str(published.get("id", ""))
    job.external_post_url = str(published.get("url", ""))
    job.published_at = timezone.now() if publish_now else None
    job.status = BlogAutomationJob.Status.PUBLISHED if publish_now else BlogAutomationJob.Status.DRAFT
    job.error_message = ""
    job.save(
        update_fields=[
            "provider",
            "external_post_id",
            "external_post_url",
            "published_at",
            "status",
            "error_message",
            "updated_at",
        ]
    )
    return published

def _process_due_blog_jobs(config: BlogAutomationConfig, limit: int = 20) -> int:
    now = timezone.now()
    due_jobs = list(
        BlogAutomationJob.objects.filter(
            config=config,
            status=BlogAutomationJob.Status.SCHEDULED,
            scheduled_for__lte=now,
        ).order_by("scheduled_for")[:limit]
    )
    processed = 0
    for job in due_jobs:
        recommendation_titles = []
        # Rebind per job. ``analysis_run`` is nullable, so a job without one used to
        # leave this unbound (UnboundLocalError on the first such job) or, worse,
        # inherit the previous iteration's run — which billed that run's owner for
        # this job's Opus spend via _meter_blog_spend. Cross-tenant, and silent.
        run = None
        if job.analysis_run_id:
            try:
                run = AnalysisRun.objects.get(pk=job.analysis_run_id)
                recommendation_titles = list(run.recommendations.values_list("title", flat=True)[:8])
            except Exception:
                recommendation_titles = []

        if not job.content_markdown or not job.title:
            draft = _generate_blog_draft(
                site_url=config.site_url,
                topic=job.topic or config.topic,
                keywords=job.keywords or config.keywords or [],
                recommendations=recommendation_titles,
                run=run,
            )
            job.title = draft.get("title", "")
            job.slug = draft.get("slug", "")
            job.meta_description = draft.get("meta_description", "")
            job.excerpt = draft.get("excerpt", "")
            job.content_markdown = draft.get("content_markdown", "")
            job.tags = draft.get("tags", [])

        if config.mode == BlogAutomationConfig.PublishMode.REVIEW_BEFORE_PUBLISH:
            job.status = BlogAutomationJob.Status.NEEDS_REVIEW
            job.error_message = ""
            job.save()
            processed += 1
            continue

        try:
            _publish_blog_job(job, publish_now=True)
        except Exception as exc:
            job.status = BlogAutomationJob.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message", "updated_at"])
        processed += 1
    return processed

def _brand_ref_for_run(run) -> str:
    """Stable string key for the run's brand (no cross-DB FK to the blog DB)."""
    if run.organization_id:
        return f"org:{run.organization_id}"
    return f"run:{run.slug}"

def _blog_source_candidates(run) -> list:
    """Reference sources for blog generation, shown as a selectable table.

    Recommended (pre-selected): top 3 competitors + Google + Reddit. Then the
    run's top cited pages (real domains AI engines surface for this brand) so the
    user can pick richer references.
    """
    from ..pipeline.citations import host_of

    rows: list = []
    seen: set = set()

    def add(name, url, type_, pos, recommended):
        dom = host_of(url or "") or (name or "").lower()
        if not dom or dom in seen:
            return
        seen.add(dom)
        rows.append(
            {
                "name": name or dom,
                "domain": dom,
                "url": url or "",
                "type": type_,
                "pos": pos,
                "recommended": recommended,
            }
        )

    try:
        for c in run.competitors.all().order_by("-scored", "-composite_score")[:3]:
            add(c.name or urlparse(c.url or "").netloc or "Competitor", c.url or "", "Competitor", 1, True)
    except Exception:
        pass
    add("Google", "https://www.google.com", "Google", 1, True)
    add("Reddit", "https://www.reddit.com", "Reddit", 1, True)

    try:
        from ..models import PromptCitation

        cites = (
            PromptCitation.objects.filter(prompt_result__prompt_track__analysis_run=run, is_brand=False)
            .order_by("position")
            .values("url", "domain", "position", "is_competitor")
        )
        for ct in cites:
            if len(rows) >= 20:
                break
            dom = ct.get("domain") or host_of(ct.get("url") or "")
            add(
                dom,
                ct.get("url") or "",
                "Competitor" if ct.get("is_competitor") else "Source",
                ct.get("position") or 1,
                False,
            )
    except Exception:
        pass

    return rows

def _blog_run_email(slug: str):
    """Resolve (run, email) for a blog-composer request from its run slug."""
    run = _safe_first(AnalysisRun.objects.filter(slug=slug), context="blog composer run lookup")
    if not run:
        return None, ""
    return run, (run.email or "").lower().strip()

def _clean_blog_posts(top_posts) -> list[dict]:
    """Coerce WordPress top_posts into the strict shape the frontend expects."""
    cleaned = []
    for post in top_posts or []:
        if post.get("id") is None:
            continue
        cleaned.append(
            {
                "id": int(post["id"]),
                "title": str(post.get("title") or ""),
                "slug": str(post.get("slug") or ""),
                "url": str(post.get("url") or ""),
                "published_at": str(post.get("published_at") or ""),
                "modified_at": str(post.get("modified_at") or ""),
            }
        )
    return cleaned

def _generate_blog_html(site_url: str, topic: str, tone: str, word_count: int) -> dict:
    """Generate a blog draft with semantic HTML body, honoring tone + length."""
    from core.llm.client import ask_llm

    prompt = f"""
You are an expert SEO + GEO content strategist.
Write a complete, publish-ready blog post.

Site URL: {site_url or "n/a"}
Primary topic: {topic}
Tone: {tone}
Target length: approximately {word_count} words

Return STRICT JSON only with keys:
title, slug, meta_description, tags, content_html

Requirements:
- title: compelling and specific
- slug: URL-safe, lowercase, hyphenated
- meta_description: max 160 chars
- tags: array of 4-8 short tags
- content_html: clean semantic HTML using only <h2>, <h3>, <p>, <ul>, <ol>,
  <li>, <strong>, <em>, <a>, <blockquote>. Do NOT include <html>, <head>,
  <body>, markdown, or code fences. Use <h2>/<h3> for structure.
"""

    raw = ask_llm(
        prompt=prompt.strip(),
        preferred_provider="gemini",
        max_tokens=2600,
        temperature=0.6,
        purpose="blog_agent.generate",
    )

    parsed = _extract_blog_json(raw) or {}
    title = str(parsed.get("title") or f"{topic} — A Practical Guide").strip()
    slug = _slugify(str(parsed.get("slug") or title))
    meta_description = str(parsed.get("meta_description") or "")[:160].strip()

    tags = parsed.get("tags")
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip() for t in tags if str(t).strip()][:8]

    content_html = str(parsed.get("content_html") or "").strip()
    if not content_html:
        content_html = (
            f"<h2>{title}</h2>"
            f"<p>{meta_description or 'A practical guide on ' + topic + '.'}</p>"
            "<h3>Why this matters</h3>"
            "<p>AI-first search rewards clear, structured, credible content.</p>"
        )

    return {
        "title": title,
        "slug": slug,
        "meta_description": meta_description,
        "tags": tags,
        "content_html": content_html,
    }

def _generate_blog_topics(run, count: int = 5) -> dict:
    """AI blog-topic ideas grounded in Search Console keywords + GA traffic + the
    prompts the brand wants to rank for, so blogs target real opportunities.

    Returns {"topics": [{title, angle, target_keyword}], "has_gsc", "has_ga"}.
    Degrades gracefully: with no GA/GSC it falls back to tracked prompts + GEO gaps.
    """
    from core.llm.client import ask_llm

    from ..services.overview_signals import build_overview_signals

    signals = build_overview_signals(run)
    gsc = signals.get("gsc") or {}
    ga = signals.get("ga") or {}
    flags = signals.get("flags") or {}

    keywords = [
        {
            "query": q.get("query", ""),
            "impressions": q.get("impressions", 0),
            "position": q.get("position", 0),
        }
        for q in (gsc.get("top_queries") or [])
        if q.get("query")
    ][:12]

    prompts: list[str] = list(
        PromptTrack.objects.filter(analysis_run=run)
        .order_by("-id")
        .values_list("prompt_text", flat=True)[:20]
    )
    for p in run.onboarding_prompts or []:
        if isinstance(p, str) and p.strip():
            prompts.append(p.strip())
        elif isinstance(p, dict) and p.get("prompt_text"):
            prompts.append(str(p["prompt_text"]).strip())
    prompts = [p for p in dict.fromkeys(prompts) if p][:20]  # dedup + cap

    geo_gaps = list(
        run.recommendations.filter(source="analyzer").order_by("priority").values_list("title", flat=True)[:8]
    )

    context = {
        "brand": signals.get("brand", {}),
        "search_console_keywords": keywords,
        "ga_summary": (
            {"sessions": ga.get("sessions"), "organic_pct": ga.get("organic_pct")} if ga else None
        ),
        "prompts_to_rank_for": prompts,
        "geo_gaps": geo_gaps,
    }

    prompt = f"""You are an SEO + GEO content strategist. Propose {count} blog post topics for this brand.

Ground EVERY topic in the DATA below. Prefer topics that:
- target a Search Console keyword the brand already gets impressions for but ranks weakly
  (high impressions / weak average position = the biggest opportunities), AND/OR
- help the brand rank for one of the prompts it is tracking (the questions users ask AI engines).

DATA (JSON):
{json.dumps(context, indent=2, default=str)}

Return STRICT JSON only:
{{"topics": [{{"title": "specific blog title", "angle": "one sentence on the angle/search intent", "target_keyword": "the exact GSC keyword or tracked prompt this targets"}}]}}

Rules:
- At most {count} topics. No generic filler — each must tie to a real keyword or tracked prompt above.
- If keyword data is sparse, base topics on the tracked prompts and GEO gaps.
- Titles must be specific and compelling. Return ONLY the JSON object.
"""

    raw = ask_llm(
        prompt=prompt.strip(),
        preferred_provider="gemini",
        max_tokens=1200,
        temperature=0.5,
        purpose=f"blog_agent.topics:run={run.pk}",
    )
    parsed = _extract_blog_json(raw) or {}
    topics = []
    for t in (parsed.get("topics") or [])[:count]:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "").strip()
        if not title:
            continue
        topics.append(
            {
                "title": title[:200],
                "angle": str(t.get("angle") or "").strip()[:300],
                "target_keyword": str(t.get("target_keyword") or "").strip()[:160],
            }
        )
    return {
        "topics": topics,
        "has_gsc": bool(flags.get("has_gsc")),
        "has_ga": bool(flags.get("has_ga")),
    }

def _auto_can_add_today(run) -> bool:
    """Whether the brand may auto-publish another backlink batch today.

    Thin delegate to the shared engine so this view and OurBacklinksView agree
    on the once-per-day gate. Imported lazily to avoid a circular import."""
    from ..services.backlink_engine import auto_can_add_today

    return auto_can_add_today(run)

