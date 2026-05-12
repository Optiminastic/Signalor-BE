import logging
import json
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests
from django.db import DatabaseError, close_old_connections
from django.db.utils import InterfaceError, OperationalError
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.subscription_utils import (
    analysis_allowed_for_email,
    get_plan_limits,
    is_plan_limits_enforcement_enabled,
    plan_limit_error_response_dict,
    prompt_batch_would_exceed,
    prompt_limit_reached,
)
from apps.integrations.models import Integration
from apps.organizations.models import Organization

from .models import (
    AnalysisRun,
    Competitor,
    GeoImprovement,
    Recommendation,
    UserAction,
    UserGamification,
    BlogAutomationConfig,
    BlogAutomationJob,
    PromptTrack,
    PromptResult,
    ACHIEVEMENTS_INFO,
    ACTION_TEMPLATES,
)
from .serializers import (
    AnalysisRunDetailSerializer,
    AnalysisRunListSerializer,
    StartAnalysisSerializer,
    UserActionSerializer,
    UserGamificationSerializer,
    CreateUserActionSerializer,
    UpdateUserActionSerializer,
    ActionTemplateSerializer,
    ActionStatsSerializer,
    BlogAutomationConfigSerializer,
    BlogAutomationJobSerializer,
    PromptTrackSerializer,
    AddPromptSerializer,
    ShareOfVoiceSerializer,
    CitationTrendPointSerializer,
    AiRecommendationSummarySerializer,
)
from .tasks import start_analysis_task

logger = logging.getLogger("apps")

CRAWL_CHECK_USER_AGENT = (
    "SignalorBot/1.0 (+https://signalor.ai; crawl-essentials-check)"
)


def _safe_first(queryset, context: str = "query"):
    try:
        return queryset.first()
    except (OperationalError, InterfaceError):
        close_old_connections()
        try:
            return queryset.first()
        except (OperationalError, InterfaceError, DatabaseError):
            logger.warning("DB unavailable during %s.", context)
            return None
    except DatabaseError:
        logger.warning("Database error during %s.", context)
        return None


def _normalize_origin(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    if not parsed.netloc:
        return ""
    scheme = parsed.scheme if parsed.scheme in ("http", "https") else "https"
    return f"{scheme}://{parsed.netloc}".rstrip("/")


def _resolve_crawl_site(email: str, run_id: int | None, analyzed_url: str) -> tuple[str, str]:
    """
    Resolve canonical site URL and source for crawl checks.
    Priority: analyzed URL -> analyzer run URL -> WordPress integration -> Shopify integration.
    """
    # Prefer the exact analyzed URL origin first to avoid checking a different
    # integration domain (e.g., myshopify.com vs custom storefront domain).
    analyzed_origin = _normalize_origin(analyzed_url)
    if analyzed_origin:
        return analyzed_origin, "analyzed_url"

    if run_id:
        run = _safe_first(
            AnalysisRun.objects.filter(pk=run_id),
            context="crawl-site run lookup",
        )
        if run and run.url:
            origin = _normalize_origin(run.url)
            if origin:
                return origin, "analyzer_run"

    org = _safe_first(
        Organization.objects.filter(owner_email=email),
        context="crawl-site org lookup",
    )
    if org:
        wp = _safe_first(
            Integration.objects.filter(
                organization=org,
                provider=Integration.Provider.WORDPRESS,
                is_active=True,
            ),
            context="crawl-site wordpress lookup",
        )
        if wp:
            site_url = _normalize_origin(str(wp.metadata.get("site_url", "")))
            if site_url:
                return site_url, "wordpress"

        shopify = _safe_first(
            Integration.objects.filter(
                organization=org,
                provider=Integration.Provider.SHOPIFY,
                is_active=True,
            ),
            context="crawl-site shopify lookup",
        )
        if shopify:
            shop_domain = str(shopify.metadata.get("shop_domain", "")).strip()
            if shop_domain:
                return _normalize_origin(f"https://{shop_domain}"), "shopify"

    return "", "unknown"


def _evaluate_crawl_file(key: str, label: str, target_url: str, content: str, status_code: int | None):
    text = (content or "").strip()
    lower = text.lower()
    exists = bool(text) and status_code == 200
    issues = []
    recommendations = []

    if not exists:
        if key == "llms":
            recommendations = [
                "Create /llms.txt with clear site summary and key content sections.",
                "Include preferred crawling guidance and support/contact section.",
            ]
        elif key == "robots":
            recommendations = [
                "Create /robots.txt with User-agent rules and sitemap reference.",
                "Ensure important content is crawlable and admin paths are restricted.",
            ]
        else:
            recommendations = [
                "Publish /sitemap.xml from CMS or SEO tooling.",
                "Include canonical URLs and keep it updated automatically.",
            ]
        return {
            "key": key,
            "label": label,
            "url": target_url,
            "found": False,
            "status": "missing",
            "http_status": status_code,
            "score": 0,
            "issues": ["File not found at expected URL."],
            "recommendations": recommendations,
            "excerpt": "",
        }

    if key == "robots":
        if "user-agent:" not in lower:
            issues.append("Missing User-agent directive.")
        if "sitemap:" not in lower:
            issues.append("Missing Sitemap declaration.")
        if "disallow:" not in lower and "allow:" not in lower:
            issues.append("Missing Allow/Disallow directives.")
        recommendations = [
            "Keep at least one User-agent rule block.",
            "Add Sitemap: https://yourdomain.com/sitemap.xml",
            "Review blocked paths to avoid hiding key pages.",
        ]
    elif key == "sitemap":
        if "<urlset" not in lower and "<sitemapindex" not in lower:
            issues.append("Invalid sitemap XML structure.")
        if "<loc>" not in lower:
            issues.append("No URL locations (<loc>) found.")
        recommendations = [
            "Serve valid XML using <urlset> or <sitemapindex>.",
            "Include canonical URLs and refresh after content updates.",
        ]
    else:
        if len(text) < 120:
            issues.append("Content is too short to guide AI crawlers.")
        if "#" not in text:
            issues.append("Missing section headers for structure.")
        if "contact" not in lower and "support" not in lower:
            issues.append("Missing contact/support guidance.")
        recommendations = [
            "Document your site purpose, key sections, and content boundaries.",
            "Use headings and concise policies for AI model usage guidance.",
        ]

    score = max(0, 100 - (len(issues) * 30))
    status_value = "good" if not issues else "needs_improvement"
    excerpt = text[:600]

    return {
        "key": key,
        "label": label,
        "url": target_url,
        "found": True,
        "status": status_value,
        "http_status": status_code,
        "score": score,
        "issues": issues,
        "recommendations": recommendations,
        "excerpt": excerpt,
    }


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


def _generate_blog_draft(
    site_url: str,
    topic: str,
    keywords: list[str],
    recommendations: list[str],
) -> dict:
    from .pipeline.llm import ask_llm

    prompt = f"""
You are an expert SEO + GEO content strategist.
Generate a long-form blog post draft for this website:

Site URL: {site_url}
Primary Topic: {topic}
Target Keywords: {", ".join(keywords) if keywords else "none"}
Technical recommendations to align with:
{chr(10).join(f"- {item}" for item in recommendations[:6]) if recommendations else "- Improve AI visibility and crawlability"}

Return STRICT JSON only with keys:
title, slug, meta_description, excerpt, content_markdown, tags

Requirements:
- title: compelling and specific
- slug: URL-safe
- meta_description: max 160 chars
- excerpt: 2-3 sentences
- content_markdown: 1200-1800 words, clear H2/H3 headings, actionable sections
- tags: array of 4-8 short tags
- Mention practical steps readers can apply.
"""

    raw = ask_llm(
        prompt=prompt.strip(),
        preferred_provider="gemini",
        max_tokens=2200,
        temperature=0.5,
        purpose="actions.blog_automation.generate",
    )

    parsed = _extract_blog_json(raw) or {}
    title = str(parsed.get("title") or f"{topic} Guide for {urlparse(site_url).netloc}").strip()
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
    chunks = [chunk.strip() for chunk in (text or "").split("\n\n") if chunk.strip()]
    if not chunks:
        return "<p></p>"
    html_chunks = []
    for chunk in chunks:
        chunk_html = chunk.replace('\n', '<br/>')
        html_chunks.append(f"<p>{chunk_html}</p>")
    return "".join(html_chunks)


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
        if run_id else None
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
    job.save(update_fields=[
        "provider", "external_post_id", "external_post_url",
        "published_at", "status", "error_message", "updated_at",
    ])
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
        if job.analysis_run_id:
            try:
                run = AnalysisRun.objects.get(pk=job.analysis_run_id)
                recommendation_titles = list(
                    run.recommendations.values_list("title", flat=True)[:8]
                )
            except Exception:
                recommendation_titles = []

        if not job.content_markdown or not job.title:
            draft = _generate_blog_draft(
                site_url=config.site_url,
                topic=job.topic or config.topic,
                keywords=job.keywords or config.keywords or [],
                recommendations=recommendation_titles,
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


class HealthCheckView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = []  

    def get(self, request):
        from django.db import connection
        from django.conf import settings
        
        health_status = {
            "status": "healthy",
            "service": "geo-be",
            "timestamp": timezone.now().isoformat(),
        }
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            health_status["database"] = "connected"
        except Exception as e:
            health_status["status"] = "unhealthy"
            health_status["database"] = f"error: {str(e)}"
            return Response(health_status, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            from django.core.cache import cache
            cache.set("health_check", "ok", 10)
            cache_value = cache.get("health_check")
            health_status["cache"] = "connected" if cache_value == "ok" else "degraded"
        except Exception as e:
            health_status["cache"] = f"error: {str(e)}"
        
        return Response(health_status, status=status.HTTP_200_OK)

class StartAnalysisView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = StartAnalysisSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = dict(serializer.validated_data)
        verify_workspace = data.pop("verify_org_workspace", False)
        cleaned_prompts = data.pop("_cleaned_prompts", None)
        if cleaned_prompts is None:
            cleaned_prompts = []
        data.pop("prompts", None)

        email = data.get("email", "")
        org_id = data.get("org_id")

        allowed, sub_err = analysis_allowed_for_email(email)
        if not allowed:
            return Response({"error": sub_err}, status=status.HTTP_403_FORBIDDEN)

        # Plan cap: each completed analysis adds up to 10 prompt tracks
        batch_exceeds, batch_msg = prompt_batch_would_exceed(email, 10)
        if batch_exceeds:
            return Response(
                plan_limit_error_response_dict(batch_msg),
                status=status.HTTP_403_FORBIDDEN,
            )

        # Block duplicate submissions: same URL still pending/running for the same org (or user)
        submitted_url = data["url"]
        in_flight_statuses = [
            AnalysisRun.Status.PENDING,
            AnalysisRun.Status.CRAWLING,
            AnalysisRun.Status.ANALYZING,
            AnalysisRun.Status.SCORING,
        ]
        if org_id:
            existing = AnalysisRun.objects.filter(
                organization_id=org_id,
                url=submitted_url,
                status__in=in_flight_statuses,
            ).first()
        elif email:
            existing = AnalysisRun.objects.filter(
                email=email,
                url=submitted_url,
                status__in=in_flight_statuses,
            ).first()
        else:
            existing = None

        if existing:
            return Response(
                {
                    "id": existing.id,
                    "slug": existing.slug,
                    "url": existing.url,
                    "status": existing.status,
                    "message": "Analysis already in progress for this URL",
                },
                status=status.HTTP_200_OK,
            )

        # Resolve organization
        org = None
        if org_id:
            org = Organization.objects.filter(pk=org_id).first()
        elif email:
            org = Organization.objects.filter(owner_email=email).first()

        run = AnalysisRun.objects.create(
            organization=org,
            url=data["url"],
            brand_name=data.get("brand_name", ""),
            country=data.get("country", ""),
            email=email,
            run_type=data["run_type"],
            status=AnalysisRun.Status.PENDING,
            onboarding_prompts=list(cleaned_prompts) if verify_workspace else [],
        )

        # Start background task
        start_analysis_task(run.id)

        return Response(
            {
                "id": run.id,
                "slug": run.slug,
                "url": run.url,
                "status": run.status,
                "message": "Analysis started",
            },
            status=status.HTTP_201_CREATED,
        )


class AnalysisRunBySlugView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = []

    def get(self, request, slug):
        try:
            run = AnalysisRun.objects.get(slug=slug)
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Project not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AnalysisRunDetailSerializer(run)
        return Response(serializer.data)


class AnalysisRunListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        org_id = request.query_params.get("org_id")
        email = request.query_params.get("email", "").lower().strip()

        if org_id:
            runs = AnalysisRun.objects.filter(organization_id=org_id)
        elif email:
            runs = AnalysisRun.objects.filter(email=email)
        else:
            return Response(
                {"error": "Either email or org_id parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AnalysisRunListSerializer(runs, many=True)
        return Response(serializer.data)


class AnalysisRunDetailView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = []  # frequently loaded by analyzer pages

    def get(self, request, run_id):
        try:
            run = AnalysisRun.objects.get(pk=run_id)
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Analysis run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AnalysisRunDetailSerializer(run)
        return Response(serializer.data)


class AnalysisRunStatusView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = []  # No throttling — this is a polling endpoint

    def get(self, request, run_id):
        try:
            run = AnalysisRun.objects.get(pk=run_id)
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Analysis run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "id": run.id,
                "status": run.status,
                "progress": run.progress,
                "composite_score": run.composite_score,
            }
        )


class ExportPDFView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, run_id):
        return self.post(request, run_id)

    def post(self, request, run_id):
        try:
            run = AnalysisRun.objects.get(pk=run_id)
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Analysis run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if run.status != AnalysisRun.Status.COMPLETE:
            return Response(
                {"error": "Analysis must be complete before exporting."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from django.template.loader import render_to_string
            from xhtml2pdf import pisa
            import io

            main_page = run.page_scores.filter(url=run.url).first()
            recommendations = run.recommendations.all()
            competitors = run.competitors.filter(scored=True)

            main_page_pillars = []
            if main_page:
                pillar_defs = [
                    ("Content Structure", main_page.content_score),
                    ("Schema Markup", main_page.schema_score),
                    ("E-E-A-T Signals", main_page.eeat_score),
                    ("Technical GEO", main_page.technical_score),
                    ("Entity Authority", main_page.entity_score),
                    ("AI Visibility", main_page.ai_visibility_score),
                ]
                for label, score in pillar_defs:
                    s = float(score or 0)
                    s = max(0.0, min(100.0, s))
                    main_page_pillars.append({
                        "label": label,
                        "score": s,
                        "remainder": 100.0 - s,
                    })

            # Sort recommendations: critical → high → medium → low.
            priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            recommendations = sorted(
                recommendations,
                key=lambda r: (priority_order.get(getattr(r, "priority", "low"), 4), r.id),
            )

            context = {
                "run": run,
                "main_page": main_page,
                "main_page_pillars": main_page_pillars,
                "recommendations": recommendations,
                "competitors": competitors,
                "ai_probes": run.ai_probes.all(),
            }

            html_string = render_to_string("analyzer/report.html", context)

            pdf_buffer = io.BytesIO()
            pisa_status = pisa.CreatePDF(html_string, dest=pdf_buffer, encoding="utf-8")

            if pisa_status.err:
                logger.error("PDF generation error for run %d: %s", run_id, pisa_status.err)
                return Response(
                    {"error": "PDF generation failed."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            pdf_buffer.seek(0)
            response = HttpResponse(pdf_buffer.read(), content_type="application/pdf")
            response["Content-Disposition"] = (
                f'attachment; filename="geo-analysis-{run.id}.pdf"'
            )
            return response

        except ImportError as exc:
            logger.error("PDF export import error: %s", exc)
            return Response(
                {"error": "PDF export library not available."},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        except Exception as exc:
            logger.error("PDF export failed for run %d: %s", run_id, exc, exc_info=True)
            return Response(
                {"error": "PDF generation failed.", "detail": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============ Gamification Views ============

class UserGamificationView(APIView):
    """Get user gamification profile"""
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        gamification, created = UserGamification.objects.get_or_create(
            user_email=email,
            defaults={"user_email": email}
        )

        serializer = UserGamificationSerializer(gamification)
        return Response(serializer.data)


class ActionTemplatesView(APIView):
    """Get available action templates"""
    permission_classes = [AllowAny]

    def get(self, request):
        templates = [
            {**template, "action_type": key}
            for key, template in ACTION_TEMPLATES.items()
        ]
        return Response(templates)


class AchievementsView(APIView):
    """Get all possible achievements"""
    permission_classes = [AllowAny]

    def get(self, request):
        achievements = [
            {**info, "code": key}
            for key, info in ACHIEVEMENTS_INFO.items()
        ]
        return Response(achievements)


class UserActionListView(APIView):
    """List user's actions"""
    permission_classes = [AllowAny]
    throttle_classes = []  # used by sidebar/actions dashboard refreshes

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        status_filter = request.query_params.get("status")
        
        actions = UserAction.objects.filter(user_email=email)
        
        if status_filter:
            actions = actions.filter(status=status_filter)
        
        serializer = UserActionSerializer(actions, many=True)
        return Response(serializer.data)


class CreateUserActionView(APIView):
    """Create a new user action"""
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = CreateUserActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Get gamification profile
        gamification, _ = UserGamification.objects.get_or_create(
            user_email=email,
            defaults={"user_email": email}
        )

        # Determine points value
        points = ACTION_TEMPLATES.get(data["action_type"], {}).get("points", 10)

        # Get related objects if provided
        recommendation = None
        if data.get("recommendation_id"):
            try:
                recommendation = Recommendation.objects.get(
                    pk=data["recommendation_id"]
                )
            except Recommendation.DoesNotExist:
                pass

        analysis_run = None
        if data.get("analysis_run_id"):
            try:
                analysis_run = AnalysisRun.objects.get(
                    pk=data["analysis_run_id"]
                )
            except AnalysisRun.DoesNotExist:
                pass

        # Create the action
        action = UserAction.objects.create(
            user_email=email,
            analysis_run=analysis_run,
            recommendation=recommendation,
            action_type=data["action_type"],
            title=data.get("title", ACTION_TEMPLATES.get(data["action_type"], {}).get("title", "Custom Action")),
            description=data.get("description", ""),
            points_value=points,
            score_before=data.get("score_before"),
            notes=data.get("notes", ""),
            status=UserAction.ActionStatus.PENDING,
        )

        return Response(
            UserActionSerializer(action).data,
            status=status.HTTP_201_CREATED
        )


class UpdateUserActionView(APIView):
    """Update a user action (start, complete, verify)"""
    permission_classes = [AllowAny]

    def post(self, request, action_id):
        try:
            action = UserAction.objects.get(pk=action_id)
        except UserAction.DoesNotExist:
            return Response(
                {"error": "Action not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = UpdateUserActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        email = action.user_email

        # Get or create gamification
        gamification, _ = UserGamification.objects.get_or_create(
            user_email=email,
            defaults={"user_email": email}
        )

        # Handle status changes
        old_status = action.status
        new_status = data.get("status")

        if new_status and new_status != old_status:
            action.status = new_status

            if new_status == UserAction.ActionStatus.IN_PROGRESS and not action.started_at:
                action.started_at = timezone.now()

            elif new_status == UserAction.ActionStatus.COMPLETED:
                if not action.completed_at:
                    action.completed_at = timezone.now()
                # Award points for completion
                gamification.add_points(action.points_value)

            elif new_status == UserAction.ActionStatus.VERIFIED:
                if not action.verified_at:
                    action.verified_at = timezone.now()
                # Store score improvement
                if data.get("score_after"):
                    action.score_after = data["score_after"]
                    if action.score_before:
                        action.score_improvement = data["score_after"] - action.score_before
                        gamification.total_score_improvement += action
                        gamification.score_improvement.total_actions_verified += 1
                        gamification.save()
                # Award bonus points for verification
                gamification.add_points(action.points_value // 2)

        # Update notes if provided
        if data.get("notes"):
            action.notes = data["notes"]

        action.save()

        # Check for new achievements
        new_achievements = gamification.check_achievements()

        return Response(
            {
                "action": UserActionSerializer(action).data,
                "gamification": UserGamificationSerializer(gamification).data,
                "new_achievements": new_achievements,
            }
        )


class ActionStatsView(APIView):
    """Get action statistics for a user"""
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        actions = UserAction.objects.filter(user_email=email)
        
        run_id = request.query_params.get("run_id")
        if run_id:
            actions = actions.filter(analysis_run_id=run_id)
        
        gamification, _ = UserGamification.objects.get_or_create(
            user_email=email,
            defaults={"user_email": email}
        )

        # Get recent achievements
        recent_achievements = [
            {**ACHIEVEMENTS_INFO.get(code, {}), "code": code}
            for code in gamification.achievements[-5:]
            if code in ACHIEVEMENTS_INFO
        ]

        stats = {
            "total_actions": actions.count(),
            "pending_actions": actions.filter(status=UserAction.ActionStatus.PENDING).count(),
            "in_progress_actions": actions.filter(status=UserAction.ActionStatus.IN_PROGRESS).count(),
            "completed_actions": actions.filter(status=UserAction.ActionStatus.COMPLETED).count(),
            "verified_actions": actions.filter(status=UserAction.ActionStatus.VERIFIED).count(),
            "total_points": gamification.total_points,
            "points_this_week": gamification.points_this_week,
            "current_streak": gamification.current_streak,
            "level": gamification.level,
            "level_name": gamification.get_level_display(),
            "level_progress": gamification.level_progress,
            "recent_achievements": recent_achievements,
        }

        return Response(stats)


class CrawlEssentialsStatusView(APIView):
    """Get llms.txt/robots.txt/sitemap.xml status for Actions submenu."""
    permission_classes = [AllowAny]
    throttle_classes = []  # sidebar/actions open frequently

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        run_id_param = request.query_params.get("run_id")
        analyzed_url = request.query_params.get("analyzed_url", "").strip()
        run_id = None
        if run_id_param:
            try:
                run_id = int(run_id_param)
            except ValueError:
                run_id = None

        try:
            site_url, source = _resolve_crawl_site(email, run_id, analyzed_url)
        except Exception:
            # Never fail hard for this diagnostics endpoint; use URL fallback.
            logger.exception("Crawl essentials: unexpected site-resolution error.")
            site_url = _normalize_origin(analyzed_url)
            source = "analyzed_url" if site_url else "unknown"

        if not site_url:
            return Response(
                {"error": "Could not resolve site URL from integrations or analysis run."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        checks = [
            ("llms", "llms.txt", "/llms.txt"),
            ("robots", "robots.txt", "/robots.txt"),
            ("sitemap", "sitemap.xml", "/sitemap.xml"),
        ]

        files = []
        for key, label, path in checks:
            target_url = f"{site_url}{path}"
            try:
                resp = requests.get(
                    target_url,
                    headers={"User-Agent": CRAWL_CHECK_USER_AGENT},
                    timeout=8,
                    allow_redirects=True,
                )
                files.append(
                    _evaluate_crawl_file(
                        key=key,
                        label=label,
                        target_url=target_url,
                        content=resp.text,
                        status_code=resp.status_code,
                    )
                )
            except requests.RequestException:
                files.append(
                    _evaluate_crawl_file(
                        key=key,
                        label=label,
                        target_url=target_url,
                        content="",
                        status_code=None,
                    )
                )

        overall_score = round(
            sum(item["score"] for item in files) / len(files), 1
        ) if files else 0.0

        return Response(
            {
                "submenu_key": "ai-crawl-essentials",
                "submenu_name": "AI Crawl Essentials",
                "site_url": site_url,
                "source": source,
                "overall_score": overall_score,
                "files": files,
            }
        )


class BlogAutomationConfigView(APIView):
    """Create/update automation settings and queue scheduled jobs."""
    permission_classes = [AllowAny]
    throttle_classes = []

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        analyzed_url = request.query_params.get("analyzed_url", "").strip()
        run_id_param = request.query_params.get("run_id")
        run_id = int(run_id_param) if str(run_id_param).isdigit() else None

        if not email:
            return Response({"error": "Email parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        config = _get_or_create_blog_config(
            email=email,
            run_id=run_id,
            analyzed_url=analyzed_url,
        )
        if not config:
            return Response({"error": "Could not resolve automation config."}, status=status.HTTP_400_BAD_REQUEST)

        if config.is_active:
            _enqueue_daily_jobs(config, days_ahead=21)
            _process_due_blog_jobs(config, limit=10)

        return Response({
            "config": BlogAutomationConfigSerializer(config).data,
        })

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        analyzed_url = str(request.data.get("analyzed_url", "")).strip()
        run_id_param = request.data.get("run_id")
        run_id = int(run_id_param) if str(run_id_param).isdigit() else None

        if not email:
            return Response({"error": "Email parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        keywords_input = request.data.get("keywords", [])
        if isinstance(keywords_input, str):
            keywords = [k.strip() for k in keywords_input.split(",") if k.strip()]
        elif isinstance(keywords_input, list):
            keywords = [str(k).strip() for k in keywords_input if str(k).strip()]
        else:
            keywords = []

        config = _get_or_create_blog_config(
            email=email,
            run_id=run_id,
            analyzed_url=analyzed_url,
            topic=str(request.data.get("topic", "")).strip(),
            keywords=keywords,
            mode=str(request.data.get("mode", "")).strip(),
            frequency_per_day=request.data.get("frequency_per_day"),
            publish_time_raw=str(request.data.get("publish_time", "")).strip(),
            is_active=request.data.get("is_active"),
        )
        if not config:
            return Response({"error": "Could not save automation config."}, status=status.HTTP_400_BAD_REQUEST)

        queued = _enqueue_daily_jobs(config, days_ahead=21) if config.is_active else 0

        return Response({
            "message": "Automation settings saved.",
            "queued_jobs": queued,
            "config": BlogAutomationConfigSerializer(config).data,
        })


class BlogAutomationCalendarView(APIView):
    """Calendar/list view for scheduled and published automated blogs."""
    permission_classes = [AllowAny]
    throttle_classes = []

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        from_date = request.query_params.get("from")
        to_date = request.query_params.get("to")
        view = request.query_params.get("view", "month")

        jobs = BlogAutomationJob.objects.filter(user_email=email).order_by("scheduled_for")
        if from_date:
            jobs = jobs.filter(scheduled_for__date__gte=from_date)
        if to_date:
            jobs = jobs.filter(scheduled_for__date__lte=to_date)

        if not from_date and not to_date:
            days = 31 if view == "month" else 7
            start = timezone.localdate() - timedelta(days=2)
            end = start + timedelta(days=days)
            jobs = jobs.filter(scheduled_for__date__gte=start, scheduled_for__date__lte=end)

        serializer = BlogAutomationJobSerializer(jobs, many=True)
        summary = {
            "scheduled": jobs.filter(status=BlogAutomationJob.Status.SCHEDULED).count(),
            "draft": jobs.filter(status=BlogAutomationJob.Status.DRAFT).count(),
            "needs_review": jobs.filter(status=BlogAutomationJob.Status.NEEDS_REVIEW).count(),
            "published": jobs.filter(status=BlogAutomationJob.Status.PUBLISHED).count(),
            "failed": jobs.filter(status=BlogAutomationJob.Status.FAILED).count(),
        }
        return Response({"summary": summary, "jobs": serializer.data})


class BlogAutomationProcessDueView(APIView):
    """Process due scheduled blogs: auto-publish or move to review queue."""
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        processed_total = 0
        configs = BlogAutomationConfig.objects.filter(user_email=email, is_active=True)
        for config in configs:
            _enqueue_daily_jobs(config, days_ahead=21)
            processed_total += _process_due_blog_jobs(config, limit=15)

        return Response({"message": "Due jobs processed.", "processed": processed_total})


class BlogAutomationGenerateView(APIView):
    """Generate AI blog draft for Actions submenu."""
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        topic = str(request.data.get("topic", "")).strip()
        analyzed_url = str(request.data.get("analyzed_url", "")).strip()
        run_id_param = request.data.get("run_id")
        run_id = int(run_id_param) if str(run_id_param).isdigit() else None

        keywords_input = request.data.get("keywords", [])
        if isinstance(keywords_input, str):
            keywords = [k.strip() for k in keywords_input.split(",") if k.strip()]
        elif isinstance(keywords_input, list):
            keywords = [str(k).strip() for k in keywords_input if str(k).strip()]
        else:
            keywords = []

        site_url, source = _resolve_crawl_site(email, run_id, analyzed_url)
        if not site_url:
            return Response(
                {"error": "Could not resolve a site URL. Connect WordPress/Shopify or provide analyzed_url."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        run = (
            _safe_first(AnalysisRun.objects.filter(pk=run_id), context="blog generate run lookup")
            if run_id else None
        )
        if not topic:
            brand = (run.brand_name if run else "") or urlparse(site_url).netloc
            topic = f"{brand} AI search strategy"

        recommendation_texts = []
        if run:
            try:
                recommendation_texts = list(run.recommendations.values_list("title", flat=True)[:8])
            except DatabaseError:
                recommendation_texts = []

        draft = _generate_blog_draft(
            site_url=site_url,
            topic=topic,
            keywords=keywords,
            recommendations=recommendation_texts,
        )

        integration, provider = _resolve_blog_integration(email)
        save_as_draft = bool(request.data.get("save_as_draft", False))
        draft_job_payload = None
        if save_as_draft:
            config = _get_or_create_blog_config(
                email=email,
                run_id=run_id,
                analyzed_url=analyzed_url,
                topic=topic,
                keywords=keywords,
                is_active=request.data.get("activate_automation"),
            )
            if config:
                job = BlogAutomationJob.objects.create(
                    config=config,
                    user_email=email,
                    analysis_run=run,
                    scheduled_for=timezone.now(),
                    provider=provider if integration else BlogAutomationConfig.PublishProvider.NONE,
                    mode=config.mode,
                    status=BlogAutomationJob.Status.DRAFT,
                    topic=topic,
                    keywords=keywords,
                    title=draft.get("title", ""),
                    slug=draft.get("slug", ""),
                    meta_description=draft.get("meta_description", ""),
                    excerpt=draft.get("excerpt", ""),
                    content_markdown=draft.get("content_markdown", ""),
                    tags=draft.get("tags", []),
                )
                draft_job_payload = BlogAutomationJobSerializer(job).data

        return Response({
            "submenu_key": "ai-blog-automation",
            "submenu_name": "AI Blog Automation",
            "site_url": site_url,
            "source": source,
            "publish_provider": provider if integration else "none",
            "draft": draft,
            "draft_job": draft_job_payload,
        })


class BlogAutomationPublishView(APIView):
    """Publish AI-generated draft to connected CMS."""
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        publish_now = bool(request.data.get("publish_now", False))
        job_id = request.data.get("job_id")
        if job_id:
            job = _safe_first(
                BlogAutomationJob.objects.filter(pk=job_id, user_email=email),
                context="blog publish job lookup",
            )
            if not job:
                return Response({"error": "Blog job not found."}, status=status.HTTP_404_NOT_FOUND)
            if not job.title or not job.content_markdown:
                return Response({"error": "Selected job has no draft content."}, status=status.HTTP_400_BAD_REQUEST)
            try:
                published = _publish_blog_job(job, publish_now=publish_now)
            except ValueError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            except Exception:
                logger.exception("Blog publish failed.")
                return Response({"error": "Unexpected error while publishing draft."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            return Response({"message": "Blog job published.", "provider": job.provider, "published": published})

        draft = request.data.get("draft") or {}
        title = str(draft.get("title", "")).strip()
        content_markdown = str(draft.get("content_markdown", "")).strip()
        if not title or not content_markdown:
            return Response({"error": "Draft title and content_markdown are required."}, status=status.HTTP_400_BAD_REQUEST)

        config = _get_or_create_blog_config(
            email=email,
            run_id=request.data.get("run_id"),
            analyzed_url=str(request.data.get("analyzed_url", "")),
            is_active=False,
        )
        if not config:
            return Response({"error": "Could not prepare publish config."}, status=status.HTTP_400_BAD_REQUEST)

        job = BlogAutomationJob.objects.create(
            config=config,
            user_email=email,
            analysis_run=config.analysis_run,
            scheduled_for=timezone.now(),
            provider=config.publish_provider,
            mode=config.mode,
            status=BlogAutomationJob.Status.DRAFT,
            topic=config.topic,
            keywords=config.keywords,
            title=title,
            slug=str(draft.get("slug", "")).strip(),
            meta_description=str(draft.get("meta_description", "")).strip(),
            excerpt=str(draft.get("excerpt", "")).strip(),
            content_markdown=content_markdown,
            tags=draft.get("tags") if isinstance(draft.get("tags"), list) else [],
        )
        try:
            published = _publish_blog_job(job, publish_now=publish_now)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Blog publish failed.")
            return Response({"error": "Unexpected error while publishing draft."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({"message": "Blog draft processed successfully.", "provider": job.provider, "published": published})


class QuickActionView(APIView):
    """Quick action - create action from recommendation"""
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        recommendation_id = request.data.get("recommendation_id")
        
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not recommendation_id:
            return Response(
                {"error": "Recommendation ID is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            recommendation = Recommendation.objects.get(pk=recommendation_id)
        except Recommendation.DoesNotExist:
            return Response(
                {"error": "Recommendation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Get the analysis run to get the score
        analysis_run = recommendation.analysis_run
        score_before = analysis_run.composite_score if analysis_run else None

        # Map recommendation category to action type
        action_type_map = {
            "schema": "add_schema",
            "technical": "add_robots",
            "eeat": "add_author",
            "entity": "post_reddit",
            "content": "add_faq",
            "ai_visibility": "post_reddit",
        }

        action_type = action_type_map.get(recommendation.category, "add_faq")
        
        # Get gamification
        gamification, _ = UserGamification.objects.get_or_create(
            user_email=email,
            defaults={"user_email": email}
        )
        points = ACTION_TEMPLATES.get(action_type, {}).get("points", 10)

        # Create action
        action = UserAction.objects.create(
            user_email=email,
            action_type=action_type,
            title=recommendation.title,
            description=recommendation.description,
            action=recommendation.action,
            points_value=points,
            status="pending",
            score_before=score_before,
            recommendation=recommendation,
            analysis_run=analysis_run,
        )

        serializer = UserActionSerializer(action)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BulkCreateUserActionView(APIView):
    """Bulk create actions from recommendations"""
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        recommendations_data = request.data.get("recommendations", [])
        
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not recommendations_data:
            return Response(
                {"error": "Recommendations list is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get gamification
        gamification, _ = UserGamification.objects.get_or_create(
            user_email=email,
            defaults={"user_email": email}
        )

        # Priority to points mapping
        priority_points = {
            "critical": 50,
            "high": 30,
            "medium": 20,
            "low": 10,
        }

        # Category to action type mapping
        action_type_map = {
            "schema": "add_schema",
            "technical": "add_robots",
            "eeat": "add_author",
            "entity": "post_reddit",
            "content": "add_faq",
            "ai_visibility": "post_reddit",
        }

        created_actions = []
        
        for rec_data in recommendations_data:
            rec_id = rec_data.get("id")
            title = rec_data.get("title", "")
            description = rec_data.get("description", "")
            action_text = rec_data.get("action", "")
            priority = rec_data.get("priority", "medium")
            analysis_run_id = rec_data.get("analysis_run_id")
            
            # Only check for duplicates if we're scanning the SAME website again
            # Check by analysis_run_id + recommendation combination
            if rec_id and analysis_run_id:
                existing = UserAction.objects.filter(
                    recommendation_id=rec_id,
                    analysis_run_id=analysis_run_id,
                    user_email=email
                ).first()
                if existing:
                    continue
            
            # Get recommendation from DB if it exists
            recommendation = None
            analysis_run = None
            score_before = None
            
            if rec_id:
                try:
                    recommendation = Recommendation.objects.get(pk=rec_id)
                    analysis_run = recommendation.analysis_run
                    score_before = analysis_run.composite_score if analysis_run else None
                except Recommendation.DoesNotExist:
                    pass
            
            # Determine action type from category
            action_type = "add_faq"
            if recommendation:
                action_type = action_type_map.get(recommendation.category, "add_faq")
            else:
                # Try to determine from title/description
                if "schema" in title.lower():
                    action_type = "add_schema"
                elif "robot" in title.lower():
                    action_type = "add_robots"
                elif "author" in title.lower() or "e-e-a-t" in title.lower():
                    action_type = "add_author"
                elif "reddit" in title.lower():
                    action_type = "post_reddit"
            
            points = priority_points.get(priority, 10)
            
            action = UserAction.objects.create(
                user_email=email,
                action_type=action_type,
                title=title,
                description=action_text,
                points_value=points,
                status="pending",
                score_before=score_before,
                recommendation=recommendation,
                analysis_run=analysis_run,
            )
            created_actions.append(action)

        serializer = UserActionSerializer(created_actions, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class GeneratePromptsView(APIView):
    """POST /api/analyzer/generate-prompts/ — AI-generate brand-relevant prompts for onboarding."""
    permission_classes = [AllowAny]

    def post(self, request):
        brand_name = request.data.get("brand_name", "").strip()
        brand_url = request.data.get("brand_url", "").strip()

        if not brand_name:
            return Response({"error": "brand_name required."}, status=status.HTTP_400_BAD_REQUEST)

        from .pipeline.prompt_tracker import generate_brand_prompts

        # Try to fetch page content for better context
        page_content = ""
        meta_desc = ""
        try:
            from .pipeline.crawler import crawl_page
            if brand_url:
                quick_crawl = crawl_page(brand_url)
                if quick_crawl.ok:
                    page_content = quick_crawl.text[:2000]
                    md = quick_crawl.soup.find("meta", attrs={"name": "description"})
                    meta_desc = (md["content"].strip() if md and md.get("content") else "")
        except Exception:
            pass

        try:
            prompts = generate_brand_prompts(
                brand_name=brand_name,
                brand_url=brand_url,
                page_content=page_content,
                meta_description=meta_desc,
                count=10,
            )
            return Response({"prompts": prompts})
        except Exception as exc:
            logger.warning("Generate prompts failed: %s", exc)
            return Response({"prompts": [
                f"What are the best alternatives to {brand_name}?",
                f"Is {brand_name} worth using?",
                f"Compare {brand_name} with competitors",
                f"What do experts recommend instead of {brand_name}?",
                f"Top tools similar to {brand_name}",
            ]})


# ============ Prompt Tracking Views ============

def _fire_and_save_prompt(track: PromptTrack, brand_name: str, brand_url: str):
    """Background worker: fires prompt across engines and saves PromptResult rows."""
    from django.db import close_old_connections
    from .pipeline.prompt_tracker import fire_prompt_across_engines
    from .pipeline.citations import persist_prompt_result, host_of, competitor_hosts_for_run

    close_old_connections()
    try:
        em = (track.analysis_run.email or "").strip()
        allowed = (
            get_plan_limits(em)["engines"]
            if is_plan_limits_enforcement_enabled() and em
            else None
        )
        engine_results = fire_prompt_across_engines(
            track.prompt_text, brand_name, brand_url, allowed_engines=allowed
        )
        brand_host = host_of(brand_url)
        rival_hosts = competitor_hosts_for_run(track.analysis_run)
        for r in engine_results:
            persist_prompt_result(track, r, brand_host, rival_hosts)
        logger.info("PromptTrack #%d: %d engine results saved", track.pk, len(engine_results))

        # Compute and persist 5-factor scores
        from .pipeline.prompt_tracker import compute_prompt_score
        all_res = list(track.results.values("brand_mentioned", "sentiment", "rank_position", "confidence", "engine"))
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
    except Exception as exc:
        logger.warning("PromptTrack #%d fire failed: %s", track.pk, exc)


class PromptListCreateView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404
        run = get_object_or_404(AnalysisRun, slug=slug)
        tracks = (
            run.prompt_tracks
               .filter(deleted_at__isnull=True)
               .select_related("analysis_run")
               .prefetch_related("results")
               .order_by("-score", "-created_at")
        )
        serializer = PromptTrackSerializer(tracks, many=True)
        return Response(serializer.data)

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404
        import threading
        run = get_object_or_404(AnalysisRun, slug=slug)
        ser = AddPromptSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)

        email = (run.email or "").strip().lower()
        reached, pl_msg = prompt_limit_reached(email)
        if reached:
            return Response(
                plan_limit_error_response_dict(pl_msg),
                status=status.HTTP_403_FORBIDDEN,
            )

        from .pipeline.prompt_tracker import classify_prompt_intent_and_type

        brand_ctx = (run.brand_name or "").strip()
        intent, prompt_type = classify_prompt_intent_and_type(
            ser.validated_data["prompt_text"],
            brand_ctx,
            (run.url or "").strip(),
        )
        track = PromptTrack.objects.create(
            analysis_run=run,
            prompt_text=ser.validated_data["prompt_text"],
            is_custom=True,
            intent=intent,
            prompt_type=prompt_type,
        )

        brand_name = run.brand_name or run.url
        brand_url = run.url
        t = threading.Thread(
            target=_fire_and_save_prompt,
            args=(track, brand_name, brand_url),
            daemon=True,
        )
        t.start()

        return Response(PromptTrackSerializer(track).data, status=status.HTTP_202_ACCEPTED)


class PromptResultDetailView(APIView):
    """GET /runs/s/<slug>/prompts/<track_id>/results/<result_id>/ — full response_text."""
    permission_classes = [AllowAny]

    def get(self, request, slug, track_id, result_id):
        from django.shortcuts import get_object_or_404
        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, pk=track_id, analysis_run=run)
        result = get_object_or_404(PromptResult, pk=result_id, prompt_track=track)
        from .serializers import PromptResultFullSerializer
        return Response(PromptResultFullSerializer(result).data)


class RecheckPromptView(APIView):
    """POST /runs/s/<slug>/prompts/<track_id>/recheck/ — re-fire one prompt now."""
    permission_classes = [AllowAny]

    def post(self, request, slug, track_id):
        from django.shortcuts import get_object_or_404
        import threading
        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(
            PromptTrack, pk=track_id, analysis_run=run, deleted_at__isnull=True
        )

        brand_name = run.brand_name or run.url
        brand_url = run.url

        def _do():
            from .pipeline.prompt_tracker import recheck_track
            from django.db import close_old_connections
            close_old_connections()
            recheck_track(track, brand_name, brand_url)

        threading.Thread(target=_do, daemon=True).start()
        return Response({"status": "rechecking"}, status=status.HTTP_202_ACCEPTED)


class PromptDeleteView(APIView):
    """DELETE /runs/s/<slug>/prompts/<track_id>/ — soft-delete a tracked prompt.

    The row is retained (flagged with `deleted_at`) so the user's historical
    count still applies toward their plan's `max_prompts`. This prevents
    deleting-and-re-adding to bypass plan limits. Usage/billing endpoints
    also count soft-deleted rows.
    """
    permission_classes = [AllowAny]

    def delete(self, request, slug, track_id):
        from django.shortcuts import get_object_or_404
        from django.utils import timezone
        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(
            PromptTrack, pk=track_id, analysis_run=run, deleted_at__isnull=True
        )
        track.deleted_at = timezone.now()
        track.save(update_fields=["deleted_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class RecheckAllPromptsView(APIView):
    """POST /runs/s/<slug>/recheck-all/ — re-fire every prompt for this run."""
    permission_classes = [AllowAny]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404
        import threading
        run = get_object_or_404(AnalysisRun, slug=slug)
        tracks = list(run.prompt_tracks.filter(deleted_at__isnull=True))

        if not tracks:
            return Response({"status": "no_tracks", "count": 0})

        brand_name = run.brand_name or run.url
        brand_url = run.url

        def _do_all():
            from .pipeline.prompt_tracker import recheck_track
            from django.db import close_old_connections
            close_old_connections()
            for track in tracks:
                try:
                    recheck_track(track, brand_name, brand_url)
                except Exception as exc:
                    logger.warning("recheck_all: track #%d failed: %s", track.pk, exc)

        threading.Thread(target=_do_all, daemon=True).start()
        return Response(
            {"status": "rechecking", "count": len(tracks)},
            status=status.HTTP_202_ACCEPTED,
        )


class ShareOfVoiceView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404
        from django.db.models import Count, Q

        run = get_object_or_404(AnalysisRun, slug=slug)
        em = (run.email or "").strip()
        valid_engine_keys = {e[0] for e in PromptResult.Engine.choices}
        if is_plan_limits_enforcement_enabled() and em:
            engines = [e for e in get_plan_limits(em)["engines"] if e in valid_engine_keys]
        else:
            engines = [e[0] for e in PromptResult.Engine.choices]
        data = []
        for engine in engines:
            qs = PromptResult.objects.filter(prompt_track__analysis_run=run, engine=engine)
            total = qs.count()
            mentioned = qs.filter(brand_mentioned=True).count()
            sov_pct = round((mentioned / total * 100), 1) if total > 0 else 0.0
            data.append({"engine": engine, "total": total, "mentioned": mentioned, "sov_pct": sov_pct})
        return Response(ShareOfVoiceSerializer(data, many=True).data)


class CitationTrendView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404
        from django.db.models.functions import TruncWeek
        from django.db.models import Count, Q
        run = get_object_or_404(AnalysisRun, slug=slug)

        em = (run.email or "").strip()
        valid_engine_keys = {e[0] for e in PromptResult.Engine.choices}
        if is_plan_limits_enforcement_enabled() and em:
            allowed = [e for e in get_plan_limits(em)["engines"] if e in valid_engine_keys]
        else:
            allowed = None

        base = PromptResult.objects.filter(prompt_track__analysis_run=run)
        if allowed is not None:
            base = base.filter(engine__in=allowed)
        qs = (
            base
            .annotate(week_start=TruncWeek("checked_at"))
            .values("week_start", "engine")
            .annotate(
                total=Count("id"),
                mentioned=Count("id", filter=Q(brand_mentioned=True)),
            )
            .order_by("week_start", "engine")
        )

        data = []
        for row in qs:
            total = row["total"]
            mentioned = row["mentioned"]
            data.append({
                "week_start": row["week_start"].date() if row["week_start"] else None,
                "engine": row["engine"],
                "rate_pct": round((mentioned / total * 100), 1) if total > 0 else 0.0,
            })
        return Response(CitationTrendPointSerializer(data, many=True).data)


class AiRecommendationSummaryView(APIView):
    """GET /runs/s/<slug>/ai-recommendation-summary/

    Aggregated answer to "how often does AI recommend this brand?" for the
    Overview citation card. Reads only existing PromptResult / PromptCitation
    rows — never fires a new AI call — so the response is honest and cheap.

    Three honest signals:
      mention_pct        — % of prompt responses that named the brand at all
      recommendation_pct — % that named the brand AND were positive sentiment
      citation_pct       — % that cited a URL on the brand's own domain
    """
    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404
        from django.db.models import Count, Q, Exists, OuterRef
        from ._cache import cached_or_compute
        from .models import PromptCitation

        run = get_object_or_404(AnalysisRun, slug=slug)

        def _compute():
            em = (run.email or "").strip()
            valid_engine_keys = {e[0] for e in PromptResult.Engine.choices}
            if is_plan_limits_enforcement_enabled() and em:
                allowed = [e for e in get_plan_limits(em)["engines"] if e in valid_engine_keys]
            else:
                allowed = None

            base = PromptResult.objects.filter(
                prompt_track__analysis_run=run,
                prompt_track__deleted_at__isnull=True,
            )
            if allowed is not None:
                base = base.filter(engine__in=allowed)

            brand_cite_exists = PromptCitation.objects.filter(
                prompt_result=OuterRef("pk"),
                is_brand=True,
            )
            annotated = base.annotate(has_brand_citation=Exists(brand_cite_exists))

            totals = annotated.aggregate(
                total=Count("id"),
                mentioned=Count("id", filter=Q(brand_mentioned=True)),
                recommended=Count(
                    "id",
                    filter=Q(brand_mentioned=True, sentiment=PromptResult.Sentiment.POSITIVE),
                ),
                cited=Count("id", filter=Q(has_brand_citation=True)),
            )
            total = totals["total"] or 0
            mentioned = totals["mentioned"] or 0
            recommended = totals["recommended"] or 0
            cited = totals["cited"] or 0

            def _pct(n: int) -> float:
                return round((n / total * 100), 1) if total > 0 else 0.0

            per_engine_rows = (
                annotated.values("engine")
                .annotate(
                    total=Count("id"),
                    mentioned=Count("id", filter=Q(brand_mentioned=True)),
                    recommended=Count(
                        "id",
                        filter=Q(brand_mentioned=True, sentiment=PromptResult.Sentiment.POSITIVE),
                    ),
                    cited=Count("id", filter=Q(has_brand_citation=True)),
                )
                .order_by("engine")
            )
            per_engine = []
            for row in per_engine_rows:
                e_total = row["total"] or 0
                e_rec = row["recommended"] or 0
                per_engine.append({
                    "engine": row["engine"],
                    "total": e_total,
                    "mentioned": row["mentioned"] or 0,
                    "recommended": e_rec,
                    "cited": row["cited"] or 0,
                    "recommendation_pct": round((e_rec / e_total * 100), 1) if e_total > 0 else 0.0,
                })

            # Up to 6 sample positive quotes so the user can audit the score.
            sample_qs = (
                annotated.filter(brand_mentioned=True, sentiment=PromptResult.Sentiment.POSITIVE)
                .exclude(response_text="")
                .select_related("prompt_track")
                .order_by("-confidence", "-checked_at")[:6]
            )
            samples = [
                {
                    "engine": pr.engine,
                    "prompt": (pr.prompt_track.prompt_text or "")[:240],
                    "quote": (pr.response_text or "")[:400],
                    "sentiment": pr.sentiment,
                }
                for pr in sample_qs
            ]

            return {
                "total": total,
                "mentioned": mentioned,
                "recommended": recommended,
                "cited": cited,
                "mention_pct": _pct(mentioned),
                "recommendation_pct": _pct(recommended),
                "citation_pct": _pct(cited),
                "per_engine": per_engine,
                "samples": samples,
            }

        data = cached_or_compute(f"ai_rec_summary:{slug}", 600, _compute)
        return Response(AiRecommendationSummarySerializer(data).data)


class CitationSourcesView(APIView):
    """GET /runs/s/<slug>/citations/ — citation source roll-up per run.

    Returns `domains` (top-cited hosts with brand/rival flags), plus convenience
    buckets `your_pages` and `rival_pages` ranked by mention frequency, so the
    frontend can render "pages AI loves" without a second query.
    """
    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404
        from django.db.models import Count
        from collections import defaultdict
        from .models import PromptCitation

        run = get_object_or_404(AnalysisRun, slug=slug)

        qs = PromptCitation.objects.filter(
            prompt_result__prompt_track__analysis_run=run,
            prompt_result__prompt_track__deleted_at__isnull=True,
        ).exclude(domain="")

        # Domain roll-up
        domain_rows = (
            qs.values("domain")
            .annotate(total=Count("id"))
            .order_by("-total")[:40]
        )
        # Pull flags for each domain (is_brand / is_competitor) separately so
        # we don't double-count or collapse mixed values.
        flag_map: dict[str, dict] = {}
        for c in qs.values("domain", "is_brand", "is_competitor"):
            f = flag_map.setdefault(c["domain"], {"is_brand": False, "is_competitor": False})
            if c["is_brand"]:
                f["is_brand"] = True
            if c["is_competitor"]:
                f["is_competitor"] = True

        # Per-engine breakdown for top domains
        engine_rows = (
            qs.filter(domain__in=[r["domain"] for r in domain_rows])
            .values("domain", "prompt_result__engine")
            .annotate(total=Count("id"))
        )
        by_engine: dict[str, dict] = defaultdict(dict)
        for r in engine_rows:
            by_engine[r["domain"]][r["prompt_result__engine"]] = r["total"]

        # Sample URL for each top domain
        sample_map: dict[str, str] = {}
        for c in qs.filter(domain__in=[r["domain"] for r in domain_rows]).values("domain", "url", "title")[:500]:
            sample_map.setdefault(c["domain"], c["url"])

        domains = []
        for row in domain_rows:
            d = row["domain"]
            flags = flag_map.get(d, {"is_brand": False, "is_competitor": False})
            domains.append({
                "domain": d,
                "total": row["total"],
                "is_brand": flags["is_brand"],
                "is_competitor": flags["is_competitor"],
                "by_engine": by_engine.get(d, {}),
                "sample_url": sample_map.get(d, ""),
            })

        # Your top-cited pages
        your_pages = (
            qs.filter(is_brand=True)
            .values("url", "title")
            .annotate(mentions=Count("id"))
            .order_by("-mentions")[:10]
        )
        rival_pages = (
            qs.filter(is_competitor=True)
            .values("url", "title", "domain")
            .annotate(mentions=Count("id"))
            .order_by("-mentions")[:10]
        )

        return Response({
            "total_citations": qs.count(),
            "brand_citations": qs.filter(is_brand=True).count(),
            "competitor_citations": qs.filter(is_competitor=True).count(),
            "domains": domains,
            "your_pages": list(your_pages),
            "rival_pages": list(rival_pages),
        })


class CompetitorListCreateView(APIView):
    """GET/POST /api/analyzer/runs/s/<slug>/competitors/"""
    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404
        run = get_object_or_404(AnalysisRun, slug=slug)
        competitors = run.competitors.all().order_by("-scored", "-composite_score")
        from .serializers import CompetitorSerializer
        return Response(CompetitorSerializer(competitors, many=True).data)

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404
        run = get_object_or_404(AnalysisRun, slug=slug)
        name = request.data.get("name", "").strip()
        url = request.data.get("url", "").strip()
        if not name or not url:
            return Response({"error": "name and url are required."}, status=status.HTTP_400_BAD_REQUEST)
        competitor = run.competitors.create(name=name, url=url)
        from .serializers import CompetitorSerializer
        return Response(CompetitorSerializer(competitor).data, status=status.HTTP_201_CREATED)


class CompetitorDetailView(APIView):
    """PATCH/DELETE /api/analyzer/runs/s/<slug>/competitors/<id>/"""
    permission_classes = [AllowAny]

    def patch(self, request, slug, competitor_id):
        from django.shortcuts import get_object_or_404
        run = get_object_or_404(AnalysisRun, slug=slug)
        competitor = get_object_or_404(Competitor, pk=competitor_id, analysis_run=run)
        if "name" in request.data:
            competitor.name = request.data["name"].strip()
        if "url" in request.data:
            competitor.url = request.data["url"].strip()
        competitor.save(update_fields=["name", "url"])
        from .serializers import CompetitorSerializer
        return Response(CompetitorSerializer(competitor).data)

    def delete(self, request, slug, competitor_id):
        from django.shortcuts import get_object_or_404
        run = get_object_or_404(AnalysisRun, slug=slug)
        competitor = get_object_or_404(Competitor, pk=competitor_id, analysis_run=run)
        competitor.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Score History ──────────────────────────────────────────────────────────

class ScoreHistoryView(APIView):
    """GET /api/analyzer/runs/history/?email=&org_id="""
    permission_classes = [AllowAny]

    def get(self, request):
        org_id = request.query_params.get("org_id")
        email = request.query_params.get("email", "").lower().strip()

        if org_id:
            qs = AnalysisRun.objects.filter(organization_id=org_id, status="complete")
        elif email:
            qs = AnalysisRun.objects.filter(email=email, status="complete")
        else:
            return Response(
                {"error": "Either email or org_id parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Order by when the run finished updating (score finalized); expose that as `date`
        # so the chart shows distinct points per analysis, not just calendar day.
        data = list(
            qs.order_by("updated_at")
            .values("id", "created_at", "updated_at", "composite_score", "slug")
        )
        result = []
        prev_score = None
        for row in data:
            score = round(row["composite_score"] or 0, 1)
            delta = None
            pct = None
            if prev_score is not None:
                delta = round(score - prev_score, 1)
                if prev_score != 0:
                    pct = round((score - prev_score) / prev_score * 100, 1)
            result.append(
                {
                    "id": row["id"],
                    "date": row["updated_at"].isoformat(),
                    "created_at": row["created_at"].isoformat(),
                    "composite_score": score,
                    "slug": row["slug"],
                    "delta_from_previous": delta,
                    "percent_change_from_previous": pct,
                }
            )
            prev_score = score
        return Response(result)


# ── Scheduled Re-analysis ─────────────────────────────────────────────────

class ScheduledAnalysisView(APIView):
    """GET/POST /api/analyzer/schedule/"""
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        org_id = request.query_params.get("org_id")
        if not email or not org_id:
            return Response({"error": "email and org_id required."}, status=status.HTTP_400_BAD_REQUEST)

        from .models import ScheduledAnalysis
        try:
            schedule = ScheduledAnalysis.objects.get(organization_id=org_id, email=email)
            from .serializers import ScheduledAnalysisSerializer
            return Response(ScheduledAnalysisSerializer(schedule).data)
        except ScheduledAnalysis.DoesNotExist:
            return Response(None, status=status.HTTP_200_OK)

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        org_id = request.data.get("org_id")
        url = request.data.get("url", "").strip()
        brand_name = request.data.get("brand_name", "").strip()
        frequency = request.data.get("frequency", "weekly")
        is_active = request.data.get("is_active", True)
        run_at_raw = request.data.get("run_at")  # optional ISO datetime

        if not email or not org_id or not url:
            return Response({"error": "email, org_id, and url required."}, status=status.HTTP_400_BAD_REQUEST)

        if frequency not in ("once", "weekly", "monthly"):
            return Response({"error": "frequency must be once/weekly/monthly."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            org = Organization.objects.get(pk=org_id)
        except Organization.DoesNotExist:
            return Response({"error": "Organization not found."}, status=status.HTTP_404_NOT_FOUND)

        # Parse explicit run_at when provided, else derive from frequency
        next_run_at = None
        if run_at_raw:
            from django.utils.dateparse import parse_datetime
            parsed = parse_datetime(str(run_at_raw))
            if not parsed:
                return Response({"error": "run_at must be an ISO datetime."}, status=status.HTTP_400_BAD_REQUEST)
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            if parsed <= timezone.now():
                return Response({"error": "run_at must be in the future."}, status=status.HTTP_400_BAD_REQUEST)
            next_run_at = parsed
        else:
            if frequency == "once":
                return Response({"error": "run_at is required when frequency=once."}, status=status.HTTP_400_BAD_REQUEST)
            delta = timedelta(days=7) if frequency == "weekly" else timedelta(days=30)
            next_run_at = timezone.now() + delta

        from .models import ScheduledAnalysis
        schedule, created = ScheduledAnalysis.objects.update_or_create(
            organization=org,
            email=email,
            defaults={
                "url": url,
                "brand_name": brand_name,
                "frequency": frequency,
                "is_active": is_active,
                "next_run_at": next_run_at,
            },
        )
        from .serializers import ScheduledAnalysisSerializer
        return Response(ScheduledAnalysisSerializer(schedule).data, status=status.HTTP_200_OK if not created else status.HTTP_201_CREATED)


# ── Auto-Fix ──────────────────────────────────────────────────────────────

class AutoFixView(APIView):
    """GET/POST /api/analyzer/runs/s/<slug>/auto-fix/"""
    permission_classes = [AllowAny]

    def get(self, request, slug):
        """Return fix status for all recommendations in this run, including cross-run fixes."""
        from django.shortcuts import get_object_or_404
        from .models import AutoFixJob

        run = get_object_or_404(AnalysisRun, slug=slug)

        # 1. Fixes for this specific run
        jobs = AutoFixJob.objects.filter(analysis_run=run).order_by("-created_at")
        seen = {}
        for job in jobs:
            if job.recommendation_id not in seen:
                seen[job.recommendation_id] = {
                    "recommendation_id": job.recommendation_id,
                    "status": job.status,
                    "message": job.response_data.get("message", job.error_message or ""),
                    "fix_type": job.fix_type,
                }

        if run.organization:
            prev_fixes = (
                AutoFixJob.objects
                .filter(analysis_run__organization=run.organization, status="success")
                .exclude(analysis_run=run)
                .select_related("recommendation")
            )
            fixed_titles = set()
            for job in prev_fixes:
                if job.recommendation and job.recommendation.title:
                    fixed_titles.add(job.recommendation.title.strip().lower())

            # Match current run's recommendations by title
            if fixed_titles:
                for rec in run.recommendations.all():
                    if rec.id not in seen and rec.title.strip().lower() in fixed_titles:
                        seen[rec.id] = {
                            "recommendation_id": rec.id,
                            "status": "success",
                            "message": "Previously fixed in an earlier analysis.",
                            "fix_type": "content_enhance",
                        }

        return Response(list(seen.values()))

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404
        from .auto_fix import apply_fixes

        run = get_object_or_404(AnalysisRun, slug=slug)
        recommendation_ids = request.data.get("recommendation_ids", [])
        email = request.data.get("email", "").lower().strip()
        org_id = request.data.get("org_id")

        if not recommendation_ids or not email:
            return Response({"error": "recommendation_ids and email are required."}, status=status.HTTP_400_BAD_REQUEST)

        # Match Shopify vs WordPress to the analyzed URL (org may have both connected)
        org = run.organization
        if not org:
            return Response({"error": "No organization linked to this run."}, status=status.HTTP_400_BAD_REQUEST)

        from .integration_resolve import resolve_store_integration_for_run

        integration = resolve_store_integration_for_run(org, run.url or "")

        if not integration:
            return Response(
                {"error": "No WordPress or Shopify integration connected. Connect one first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        recommendations = Recommendation.objects.filter(
            id__in=recommendation_ids, analysis_run=run
        )

        results = apply_fixes(run, integration, list(recommendations))
        return Response(results)


class AutoFixPreviewView(APIView):
    """POST /api/analyzer/runs/s/<slug>/auto-fix/preview/ — generate fix preview without applying."""
    permission_classes = [AllowAny]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404
        from .auto_fix import generate_fix_preview

        run = get_object_or_404(AnalysisRun, slug=slug)
        rec_id = request.data.get("recommendation_id")
        email = request.data.get("email", "").lower().strip()

        if not rec_id or not email:
            return Response({"error": "recommendation_id and email required."}, status=status.HTTP_400_BAD_REQUEST)

        org = run.organization
        if not org:
            return Response({"error": "No organization linked."}, status=status.HTTP_400_BAD_REQUEST)

        from .integration_resolve import resolve_store_integration_for_run
        integration = resolve_store_integration_for_run(org, run.url or "")

        if not integration:
            return Response({"error": "No store integration connected."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rec = Recommendation.objects.get(id=rec_id, analysis_run=run)
        except Recommendation.DoesNotExist:
            return Response({"error": "Recommendation not found."}, status=status.HTTP_404_NOT_FOUND)

        preview = generate_fix_preview(run, integration, rec)
        return Response(preview)


class AutoFixApproveView(APIView):
    """POST /api/analyzer/runs/s/<slug>/auto-fix/approve/ — apply a previewed fix via plugin."""
    permission_classes = [AllowAny]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404
        from .auto_fix import apply_approved_fix
        from .models import AutoFixJob

        run = get_object_or_404(AnalysisRun, slug=slug)
        rec_id = request.data.get("recommendation_id")
        approved_content = request.data.get("content", "")
        fix_type = request.data.get("fix_type", "content")

        if not rec_id:
            return Response({"error": "recommendation_id required."}, status=status.HTTP_400_BAD_REQUEST)

        org = run.organization
        if not org:
            return Response({"error": "No organization linked."}, status=status.HTTP_400_BAD_REQUEST)

        from .integration_resolve import resolve_store_integration_for_run
        integration = resolve_store_integration_for_run(org, run.url or "")

        if not integration:
            return Response({"error": "No store integration connected."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rec = Recommendation.objects.get(id=rec_id, analysis_run=run)
        except Recommendation.DoesNotExist:
            return Response({"error": "Recommendation not found."}, status=status.HTTP_404_NOT_FOUND)

        result = apply_approved_fix(run, integration, rec, approved_content, fix_type)

        # Audit row — must include integration FK; failures here must not mask a successful apply
        raw_status = result.get("status") or "failed"
        allowed = {s.value for s in AutoFixJob.Status}
        job_status = raw_status if raw_status in allowed else "failed"
        err_msg = (
            result.get("message", "")
            if raw_status in ("failed", "error", "skipped")
            else ""
        )
        try:
            AutoFixJob.objects.create(
                analysis_run=run,
                recommendation=rec,
                integration=integration,
                fix_type=fix_type,
                status=job_status,
                response_data=result,
                error_message=err_msg,
            )
        except Exception:
            logger.exception(
                "Failed to persist AutoFixJob after apply_approved_fix (run=%s rec=%s)",
                run.id,
                rec.id,
            )

        return Response(result)


class AutoFixVerifyView(APIView):
    """POST /api/analyzer/runs/s/<slug>/auto-fix/verify/ — re-fetch the page and verify the fix heuristically."""
    permission_classes = [AllowAny]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404
        from .models import AutoFixJob
        from .recommendation_verify import verify_recommendation_fix

        run = get_object_or_404(AnalysisRun, slug=slug)
        rec_id = request.data.get("recommendation_id")

        if not rec_id:
            return Response({"error": "recommendation_id required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rec = Recommendation.objects.get(id=rec_id, analysis_run=run)
        except Recommendation.DoesNotExist:
            return Response({"error": "Recommendation not found."}, status=status.HTTP_404_NOT_FOUND)

        result = verify_recommendation_fix(run, rec)
        st = result.get("status")
        if st == "verified":
            job_status = AutoFixJob.Status.VERIFIED
        elif st == "manual":
            job_status = AutoFixJob.Status.MANUAL
        else:
            job_status = AutoFixJob.Status.FAILED
        try:
            AutoFixJob.objects.create(
                analysis_run=run,
                recommendation=rec,
                integration=None,
                fix_type=result.get("fix_type") or "verification",
                status=job_status,
                response_data=result,
                error_message=""
                if st == "verified"
                else (result.get("message") or "")[:500],
            )
        except Exception:
            logger.exception("Failed to create verify record (run=%s rec=%s)", run.id, rec.id)

        return Response(
            {
                "recommendation_id": rec.id,
                "status": result.get("status", "failed"),
                "message": result.get("message", ""),
                "fix_type": result.get("fix_type", "verification"),
            }
        )


# ── AI Chat (GEO Assistant with analysis context) ────────────────────────────

class AiChatView(APIView):
    """POST /api/analyzer/runs/s/<slug>/chat/ — AI chat with full analysis context."""
    permission_classes = [AllowAny]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        message = request.data.get("message", "").strip()
        history = request.data.get("history", [])

        if not message:
            return Response({"error": "message required"}, status=status.HTTP_400_BAD_REQUEST)

        # Build context from THIS run's stored data only — no re-crawling
        page_score = run.page_scores.filter(url=run.url).first()
        all_page_scores = list(run.page_scores.all().values("url", "content_score", "schema_score", "eeat_score", "technical_score"))
        recs = list(run.recommendations.values_list("title", "priority", "pillar", "description")[:15])

        # Extract brand info from stored analysis details
        meta_desc = ""
        page_title = ""
        word_count = 0
        site_discovery = {}

        if page_score and page_score.content_details:
            checks = page_score.content_details.get("checks", {})
            intent = checks.get("intent_clarity", {})
            coverage = checks.get("coverage_depth", {})
            word_count = checks.get("word_count", coverage.get("word_count", 0))
            site_discovery = checks.get("site_discovery", {})
            meta_desc = intent.get("has_meta_description", "")

        if page_score and page_score.technical_details:
            tech_checks = page_score.technical_details.get("checks", {})
            infra = tech_checks.get("infrastructure", {})
            ai_read = tech_checks.get("ai_readability", {})

        # Build context
        brand = run.brand_name or run.url
        context_parts = [
            f"Brand: {brand}",
            f"URL: {run.url}",
            f"Word count on homepage: {word_count}",
        ]

        if site_discovery:
            context_parts.append(f"Site structure: {site_discovery.get('products', 0)} products, "
                                 f"{site_discovery.get('collections', 0)} collections, "
                                 f"{site_discovery.get('pages', 0)} pages, "
                                 f"{site_discovery.get('blog_posts', 0)} blog posts")

        context_parts.append(f"\n--- EXACT SCORES (from this analysis) ---")
        context_parts.append(f"Overall GEO Score: {round(run.composite_score, 1)}/100")

        if page_score:
            context_parts.extend([
                f"Technical: {round(page_score.technical_score, 1)}/100",
                f"Schema: {round(page_score.schema_score, 1)}/100",
                f"Content: {round(page_score.content_score, 1)}/100",
                f"E-E-A-T: {round(page_score.eeat_score, 1)}/100",
                f"Entity: {round(page_score.entity_score, 1)}/100",
                f"AI Visibility: {round(page_score.ai_visibility_score, 1)}/100",
            ])

            # Add detail breakdowns if available
            if page_score.content_details and page_score.content_details.get("checks"):
                cc = page_score.content_details["checks"]
                context_parts.append(f"\nContent breakdown: intent={cc.get('intent_score', '?')}, "
                                     f"coverage={cc.get('coverage_score', '?')}, "
                                     f"density={cc.get('density_score', '?')}, "
                                     f"structure={cc.get('structure_score', '?')}")

            if page_score.eeat_details and page_score.eeat_details.get("checks"):
                ec = page_score.eeat_details["checks"]
                context_parts.append(f"E-E-A-T breakdown: identity={ec.get('identity_score', '?')}, "
                                     f"evidence={ec.get('evidence_score', '?')}, "
                                     f"experience={ec.get('experience_score', '?')}, "
                                     f"trust={ec.get('trust_score', '?')}")

            if page_score.technical_details and page_score.technical_details.get("checks"):
                tc = page_score.technical_details["checks"]
                context_parts.append(f"Technical breakdown: infra={tc.get('infra_score', '?')}, "
                                     f"perf={tc.get('perf_score', '?')}, "
                                     f"crawl={tc.get('crawl_score', '?')}, "
                                     f"ai_read={tc.get('ai_read_score', '?')}, "
                                     f"struct={tc.get('struct_score', '?')}")

        if len(all_page_scores) > 1:
            context_parts.append(f"\nPages analyzed: {len(all_page_scores)}")
            for ps in all_page_scores[:5]:
                context_parts.append(f"  - {ps['url']}: content={round(ps['content_score'],1)}, "
                                     f"schema={round(ps['schema_score'],1)}, eeat={round(ps['eeat_score'],1)}")

        if recs:
            context_parts.append("\nRecommendations:")
            for title, priority, pillar, desc in recs:
                context_parts.append(f"- [{priority}] {pillar}: {title} — {desc[:120]}")

        # Include findings (specific issues detected)
        if page_score and page_score.content_details:
            findings = page_score.content_details.get("findings", [])
            if findings:
                context_parts.append(f"\nContent issues found: {', '.join(findings)}")
        if page_score and page_score.eeat_details:
            findings = page_score.eeat_details.get("findings", [])
            if findings:
                context_parts.append(f"E-E-A-T issues found: {', '.join(findings)}")
        if page_score and page_score.technical_details:
            findings = page_score.technical_details.get("findings", [])
            if findings:
                context_parts.append(f"Technical issues found: {', '.join(findings)}")

        context = "\n".join(context_parts)

        # Detect platform from URL
        is_shopify = ".myshopify.com" in (run.url or "")

        # Build system prompt
        platform_name = "Shopify" if is_shopify else "WordPress"
        system_prompt = f"""You are Signalor's GEO (Generative Engine Optimization) assistant for {brand}.
You help D2C brand owners improve their AI visibility — how often ChatGPT, Gemini, and Perplexity recommend their brand.

The user is a {platform_name} store owner. They are NOT a developer. Give instructions using ONLY the {platform_name} admin UI — never tell them to edit code, Liquid templates, or theme files.

{context}

RESPONSE FORMAT RULES:
- Use **bold** for important terms and action items.
- Use numbered steps (1. 2. 3.) for instructions.
- Use bullet points (- ) for lists.
- Keep each step short and specific: "Go to X → click Y → do Z".
- Give EXACT {platform_name} Admin paths. Example: "Shopify Admin → Online Store → Pages → click on your page → edit the title"
- Include specific examples relevant to their brand when possible.
- If they ask "how to fix" something, give step-by-step {platform_name} instructions immediately — don't explain theory first.
- Maximum 4-5 short paragraphs or a numbered list of 5-8 steps.
- Be encouraging but direct. No fluff.
- ONLY use the EXACT scores shown above. Never guess.
- If you don't know something about their products, say so."""

        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-8:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": message})

        # Call Gemini via the LLM pipeline
        try:
            from .pipeline.llm import ask_llm
            # Build a single prompt with conversation context
            conv = ""
            for m in messages:
                if m["role"] == "system":
                    conv += f"System: {m['content']}\n\n"
                elif m["role"] == "user":
                    conv += f"User: {m['content']}\n"
                elif m["role"] == "assistant":
                    conv += f"Assistant: {m['content']}\n"
            conv += "Assistant:"

            reply = ask_llm(conv, preferred_provider="gemini", max_tokens=800, purpose="GEO Chat")

            if not reply:
                reply = "I'm having trouble connecting right now. Please try again in a moment."

            return Response({"reply": reply.strip()})
        except Exception as exc:
            logger.warning("AI Chat failed: %s", exc)
            return Response({"reply": "Sorry, I couldn't process that right now. Please try again."})


# ── GEO improvements (fix plan + apply) ─────────────────────────────────────

class GeoImprovementsView(APIView):
    """GET /api/analyzer/runs/s/<slug>/geo-improvements/"""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .pipeline.geo_improvement import get_all_recommendations_fix_plan

        run = get_object_or_404(AnalysisRun, slug=slug)
        qs = GeoImprovement.objects.filter(analysis_run=run).order_by("-created_at")
        improvements = []
        for imp in qs:
            improvements.append(
                {
                    "id": imp.id,
                    "provider": imp.provider,
                    "improvement_type": imp.improvement_type,
                    "status": imp.status,
                    "resource_type": imp.resource_type,
                    "resource_id": imp.resource_id,
                    "resource_title": imp.resource_title,
                    "field_name": imp.field_name,
                    "old_value": imp.old_value,
                    "new_value": imp.new_value,
                    "error_message": imp.error_message or "",
                    "applied_at": imp.applied_at.isoformat() if imp.applied_at else None,
                }
            )
        applied_count = sum(1 for i in improvements if i["status"] == "applied")
        failed_count = sum(1 for i in improvements if i["status"] == "failed")
        suggested_fixes = get_all_recommendations_fix_plan(run)
        return Response(
            {
                "total": len(improvements),
                "applied_count": applied_count,
                "failed_count": failed_count,
                "improvements": improvements,
                "suggested_fixes": suggested_fixes,
            }
        )


class ApplyGeoFixesAndReanalyzeView(APIView):
    """POST /api/analyzer/runs/s/<slug>/apply-geo-fixes/"""

    permission_classes = [AllowAny]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .pipeline.geo_improvement import get_all_recommendations_fix_plan, run_geo_improvements

        run = get_object_or_404(AnalysisRun, slug=slug)
        reanalyze = bool(request.data.get("reanalyze", False))

        applied = run_geo_improvements(run.id)
        plan_len = len(get_all_recommendations_fix_plan(run))

        next_run_payload = None
        if reanalyze:
            allowed, sub_err = analysis_allowed_for_email(run.email or "")
            if not allowed:
                return Response({"error": sub_err}, status=status.HTTP_403_FORBIDDEN)

            batch_exceeds, batch_msg = prompt_batch_would_exceed(run.email or "", 10)
            if batch_exceeds:
                return Response(
                    plan_limit_error_response_dict(batch_msg),
                    status=status.HTTP_403_FORBIDDEN,
                )

            new_run = AnalysisRun.objects.create(
                organization=run.organization,
                url=run.url,
                brand_name=run.brand_name or "",
                country=run.country or "",
                email=run.email or "",
                run_type=run.run_type,
                status=AnalysisRun.Status.PENDING,
            )
            start_analysis_task(new_run.id)
            next_run_payload = {"id": new_run.id, "slug": new_run.slug}

        return Response(
            {
                "message": "GEO fixes applied.",
                "requested_fixes": plan_len,
                "applied_count": applied,
                "next_run": next_run_payload,
            }
        )


# ============================================================================
# Sitemap audit
# ============================================================================

class SitemapAuditStartView(APIView):
    """POST /runs/s/<slug>/sitemap/  — kick off an async sitemap audit."""
    permission_classes = [AllowAny]

    def post(self, request, slug):
        import threading
        from django.shortcuts import get_object_or_404
        from .models import SitemapAudit
        from .pipeline.sitemap_audit import run_sitemap_audit, HARD_URL_CAP
        from .serializers import SitemapAuditSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        audit = SitemapAudit.objects.create(
            analysis_run=run,
            status=SitemapAudit.Status.QUEUED,
            crawl_limit=HARD_URL_CAP,
        )

        def _do(aid):
            try:
                close_old_connections()
                run_sitemap_audit(aid)
            except Exception:
                logger.exception("run_sitemap_audit thread failed")

        threading.Thread(target=_do, args=(audit.id,), daemon=True).start()

        return Response(
            SitemapAuditSerializer(audit).data,
            status=status.HTTP_202_ACCEPTED,
        )


class SitemapAuditDetailView(APIView):
    """GET /runs/s/<slug>/sitemap/  — latest audit summary + paginated pages."""
    permission_classes = [AllowAny]

    ALLOWED_SORTS = {
        "url": "url",
        "-url": "-url",
        "status": "status_code",
        "-status": "-status_code",
        "ai_score": "ai_score",
        "-ai_score": "-ai_score",
        "words": "word_count",
        "-words": "-word_count",
        "lcp": "lcp_ms",
        "-lcp": "-lcp_ms",
        "fcp": "fcp_ms",
        "-fcp": "-fcp_ms",
        "ttfb": "ttfb_ms",
        "-ttfb": "-ttfb_ms",
    }

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404
        from .models import SitemapAudit
        from .serializers import SitemapAuditSerializer, SitemapAuditPageSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        audit = (
            SitemapAudit.objects.filter(analysis_run=run).order_by("-created_at").first()
        )
        if audit is None:
            return Response({"audit": None, "pages": [], "total": 0})

        qs = audit.pages.all()
        state = request.GET.get("state")
        if state:
            qs = qs.filter(state=state)
        severity = request.GET.get("severity")
        if severity:
            qs = qs.filter(severity=severity)
        q = (request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(url__icontains=q)

        sort = request.GET.get("sort", "-ai_score")
        qs = qs.order_by(self.ALLOWED_SORTS.get(sort, "-ai_score"), "id")

        total = qs.count()
        try:
            page_size = min(max(int(request.GET.get("page_size", 50)), 1), 200)
            page = max(int(request.GET.get("page", 1)), 1)
        except ValueError:
            page_size, page = 50, 1
        start_idx = (page - 1) * page_size
        rows = list(qs[start_idx:start_idx + page_size])

        return Response({
            "audit": SitemapAuditSerializer(audit).data,
            "pages": SitemapAuditPageSerializer(rows, many=True).data,
            "total": total,
            "page": page,
            "page_size": page_size,
        })


class AgentLogView(APIView):
    """GET /runs/s/<slug>/agent-log/  — stub; returns empty entries + integration slots."""
    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404
        from .models import AgentLogEntry
        from .serializers import AgentLogEntrySerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        entries = AgentLogEntry.objects.filter(analysis_run=run).order_by("-ts")[:100]
        return Response({
            "entries": AgentLogEntrySerializer(entries, many=True).data,
            "integrations": [
                {"name": "Cloudflare Logpush", "key": "cloudflare", "connected": False, "status": "coming_soon"},
                {"name": "Vercel Edge Logs", "key": "vercel", "connected": False, "status": "coming_soon"},
            ],
        })


# ============================================================================
# Schema Watchtower
# ============================================================================

class SchemaWatchStartView(APIView):
    """POST /runs/s/<slug>/schema-watch/  — kick off a schema validation run."""
    permission_classes = [AllowAny]

    def post(self, request, slug):
        import threading
        from django.shortcuts import get_object_or_404
        from .models import SchemaWatch
        from .pipeline.schema_watch import run_schema_watch
        from .serializers import SchemaWatchSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        watch = SchemaWatch.objects.create(
            analysis_run=run,
            status=SchemaWatch.Status.QUEUED,
        )

        def _do(wid):
            try:
                close_old_connections()
                run_schema_watch(wid)
            except Exception:
                logger.exception("run_schema_watch thread failed")

        threading.Thread(target=_do, args=(watch.id,), daemon=True).start()

        return Response(
            SchemaWatchSerializer(watch).data,
            status=status.HTTP_202_ACCEPTED,
        )


class SchemaWatchDetailView(APIView):
    """GET /runs/s/<slug>/schema-watch/  — latest watch summary + pages."""
    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404
        from .models import SchemaWatch
        from .serializers import SchemaWatchSerializer, SchemaWatchPageSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        watch = (
            SchemaWatch.objects.filter(analysis_run=run).order_by("-created_at").first()
        )
        if watch is None:
            return Response({"watch": None, "pages": [], "total": 0})

        qs = watch.pages.all()
        severity = request.GET.get("severity")
        if severity:
            qs = qs.filter(severity=severity)
        kind = request.GET.get("kind")
        if kind:
            qs = qs.filter(page_kind=kind)
        q = (request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(url__icontains=q)

        # Sort fail first, then warn, then ok; within each, by URL
        qs = qs.extra(
            select={"_sev_rank": "CASE severity WHEN 'fail' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END"},
        ).order_by("_sev_rank", "url")

        total = qs.count()
        try:
            page_size = min(max(int(request.GET.get("page_size", 100)), 1), 200)
            page = max(int(request.GET.get("page", 1)), 1)
        except ValueError:
            page_size, page = 100, 1
        start_idx = (page - 1) * page_size
        rows = list(qs[start_idx:start_idx + page_size])

        return Response({
            "watch": SchemaWatchSerializer(watch).data,
            "pages": SchemaWatchPageSerializer(rows, many=True).data,
            "total": total,
            "page": page,
            "page_size": page_size,
        })


class RankAuditStartView(APIView):
    """POST /runs/s/<slug>/rank/start/ — kick off an async rank audit."""
    permission_classes = [AllowAny]

    def post(self, request, slug):
        import threading
        from django.shortcuts import get_object_or_404
        from .models import RankAudit
        from .pipeline.rank_tracker import run_rank_audit
        from .serializers import RankAuditSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)

        already = (
            RankAudit.objects
            .filter(analysis_run=run, status__in=[RankAudit.Status.QUEUED, RankAudit.Status.RUNNING])
            .first()
        )
        if already is not None:
            return Response(
                {"detail": "An audit is already running for this run.",
                 "audit": RankAuditSerializer(already).data},
                status=status.HTTP_409_CONFLICT,
            )

        audit = RankAudit.objects.create(
            analysis_run=run,
            status=RankAudit.Status.QUEUED,
        )

        def _do(aid):
            try:
                close_old_connections()
                run_rank_audit(aid)
            except Exception:
                logger.exception("run_rank_audit thread failed")

        threading.Thread(target=_do, args=(audit.id,), daemon=True).start()

        return Response(
            RankAuditSerializer(audit).data,
            status=status.HTTP_202_ACCEPTED,
        )


class RankAuditDetailView(APIView):
    """GET /runs/s/<slug>/rank/ — latest audit summary + queries + their results."""
    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404
        from .models import RankAudit
        from .serializers import RankAuditSerializer, RankQuerySerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        audit = (
            RankAudit.objects.filter(analysis_run=run).order_by("-created_at").first()
        )
        if audit is None:
            return Response({"audit": None, "queries": []})

        queries_qs = audit.queries.all().prefetch_related("results")

        surface = request.GET.get("surface")
        query_id = request.GET.get("query_id")
        q_substr = (request.GET.get("q") or "").strip()
        only_brand = request.GET.get("only_brand") in ("1", "true", "True")

        if query_id:
            try:
                queries_qs = queries_qs.filter(id=int(query_id))
            except (TypeError, ValueError):
                pass

        if q_substr:
            queries_qs = queries_qs.filter(prompt_text__icontains=q_substr)

        queries = list(queries_qs.order_by("rank", "id"))

        data = []
        for q in queries:
            results = list(q.results.all())
            if surface:
                results = [r for r in results if r.surface == surface]
            if only_brand:
                results = [r for r in results if r.is_brand_mentioned]
            results.sort(key=lambda r: (r.surface, r.position))
            q._prefetched_results = results  # type: ignore[attr-defined]

        serialized = []
        for q in queries:
            payload = {
                "id": q.id,
                "prompt_text": q.prompt_text,
                "rank": q.rank,
                "brand_mention_count": q.brand_mention_count,
                "status": q.status,
                "error_message": q.error_message,
                "results": [
                    {
                        "id": r.id,
                        "surface": r.surface,
                        "position": r.position,
                        "url": r.url,
                        "domain": r.domain,
                        "title": r.title,
                        "snippet": r.snippet,
                        "engine": r.engine,
                        "response_text": r.response_text,
                        "sentiment": r.sentiment,
                        "is_brand_mentioned": r.is_brand_mentioned,
                        "competitors_mentioned": r.competitors_mentioned,
                        "upvotes": r.upvotes,
                        "subreddit": r.subreddit,
                        "checked_at": r.checked_at.isoformat() if r.checked_at else None,
                    }
                    for r in getattr(q, "_prefetched_results", q.results.all())
                ],
            }
            serialized.append(payload)

        return Response({
            "audit": RankAuditSerializer(audit).data,
            "queries": serialized,
        })


class RankAuditRefreshQueryView(APIView):
    """POST /runs/s/<slug>/rank/query/<query_id>/refresh/ — re-fetch one query across all surfaces."""
    permission_classes = [AllowAny]

    def post(self, request, slug, query_id):
        import threading
        from django.shortcuts import get_object_or_404
        from .models import RankAudit, RankQuery, RankResult
        from .pipeline.rank_tracker import audit_query
        from .serializers import RankQuerySerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        audit = (
            RankAudit.objects.filter(analysis_run=run).order_by("-created_at").first()
        )
        if audit is None:
            return Response({"detail": "No audit exists for this run."}, status=status.HTTP_404_NOT_FOUND)

        query = get_object_or_404(RankQuery, id=query_id, audit=audit)

        RankResult.objects.filter(query=query).delete()
        query.status = RankQuery.Status.QUEUED
        query.brand_mention_count = 0
        query.error_message = ""
        query.save(update_fields=["status", "brand_mention_count", "error_message"])

        from urllib.parse import urlparse as _urlparse
        from .pipeline.rank_tracker import _derive_geo
        brand_names = [n for n in (run.brand_name,) if n]
        try:
            brand_domain = _urlparse(run.url or "").netloc.lower().replace("www.", "")
        except Exception:
            brand_domain = ""
        if brand_domain:
            brand_names.append(brand_domain)
        try:
            competitor_names = [
                c for c in run.competitors.values_list("name", flat=True) if c
            ]
        except Exception:
            competitor_names = []
        gl = (_derive_geo(run).get("gl") or "")

        def _do(qid):
            close_old_connections()
            try:
                from .models import RankQuery as _RQ
                q = _RQ.objects.get(pk=qid)
                audit_query(q, brand_names, competitor_names, brand_domain=brand_domain, gl=gl)
            except Exception:
                logger.exception("refresh rank query thread failed")

        threading.Thread(target=_do, args=(query.id,), daemon=True).start()

        return Response(RankQuerySerializer(query).data, status=status.HTTP_202_ACCEPTED)
