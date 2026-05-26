import json
import logging
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
from core.middleware import _client_ip
from core.throttling import (
    AiChatThrottle,
    AuditStartThrottle,
    AuthSendThrottle,
    DataForSEOThrottle,
    ExpensiveThrottle,
    PollingThrottle,
)

from .models import (
    ACHIEVEMENTS_INFO,
    ACTION_TEMPLATES,
    AnalysisRun,
    BlogAutomationConfig,
    BlogAutomationJob,
    Competitor,
    GeoImprovement,
    PromptResult,
    PromptTrack,
    Recommendation,
    UserAction,
    UserGamification,
)
from .onboarding_security import (
    mint_token as _mint_onboarding_token,
)
from .onboarding_security import (
    turnstile_enabled as _turnstile_enabled,
)
from .onboarding_security import (
    verify_token as _verify_onboarding_token,
)
from .onboarding_security import (
    verify_turnstile as _verify_turnstile,
)
from .serializers import (
    AddPromptSerializer,
    AiRecommendationSummarySerializer,
    AnalysisRunDetailSerializer,
    AnalysisRunListSerializer,
    BlogAutomationConfigSerializer,
    BlogAutomationJobSerializer,
    CitationTrendPointSerializer,
    CreateUserActionSerializer,
    PromptTrackSerializer,
    ShareOfVoiceSerializer,
    StartAnalysisSerializer,
    UpdateUserActionSerializer,
    UserActionSerializer,
    UserGamificationSerializer,
)
from .tasks import start_analysis_task

logger = logging.getLogger("apps")

CRAWL_CHECK_USER_AGENT = "SignalorBot/1.0 (+https://signalor.ai; crawl-essentials-check)"


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
        chunk_html = chunk.replace("\n", "<br/>")
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
    throttle_classes = [PollingThrottle]

    def get(self, request):
        from django.db import connection

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


class OnboardingStartView(APIView):
    """
    POST /api/analyzer/onboarding-start/

    Mints a short-lived signed token (~15 min) that downstream public AI
    endpoints (currently /generate-prompts/) require in the
    ``X-Onboarding-Token`` header.

    Body (optional):
      { "turnstile_token": "<cf turnstile response>" }

    If ``TURNSTILE_SECRET`` is configured server-side, ``turnstile_token`` is
    required and verified against Cloudflare. Without it, /onboarding-start
    only requires passing the global IP middleware + this throttle — that
    alone breaks rotating-IP wallet-drain on /generate-prompts since each
    fresh IP must round-trip here first (heavily throttled).
    """

    permission_classes = [AllowAny]
    throttle_classes = [AuthSendThrottle]

    def post(self, request):
        client_ip = _client_ip(request)
        turnstile_token = (request.data.get("turnstile_token") or "").strip()
        if _turnstile_enabled():
            ok, reason = _verify_turnstile(turnstile_token, client_ip)
            if not ok:
                logger.warning("onboarding_start turnstile fail ip=%s reason=%s", client_ip, reason)
                return Response(
                    {"detail": "Bot check failed. Refresh and try again.", "reason": reason},
                    status=status.HTTP_403_FORBIDDEN,
                )
        token = _mint_onboarding_token(client_ip)
        return Response(
            {
                "token": token,
                "expires_in": 900,
                "turnstile_enabled": _turnstile_enabled(),
            }
        )


class StartAnalysisView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

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

        # Plan cap: each completed analysis adds up to 10 prompt tracks.
        # Anonymous (no email) requests are free-tool scans — no account to cap.
        if email:
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
            storefront_password=data.get("storefront_password", ""),
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
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        try:
            run = (
                AnalysisRun.objects.select_related("brand_visibility", "organization")
                .prefetch_related(
                    "page_scores",
                    "competitors",  # remove this line to stop loading competitors
                    "recommendations",
                    "ai_probes",
                )
                .get(slug=slug)
            )
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

        # Heavy JSONFields (llm_logs, onboarding_prompts) aren't in the list
        # serializer — defer them so we don't ship hundreds of KB per row.
        runs = runs.defer("llm_logs", "onboarding_prompts").order_by("-created_at")
        serializer = AnalysisRunListSerializer(runs, many=True)
        return Response(serializer.data)


class AnalysisRunDetailView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]  # frequently loaded by analyzer pages

    def get(self, request, run_id):
        from django.db.models import Prefetch

        try:
            # Pre-load every related collection the serializer touches so the
            # response is one batch of queries instead of five sequential
            # cross-region round trips. brand_visibility is a OneToOne —
            # select_related. Reverse-FKs use prefetch_related.
            #
            # RecommendationSerializer.get_can_auto_fix() reads obj.analysis_run.url,
            # so we chain a select_related to avoid one query per recommendation.
            # We also defer llm_logs (~128 KB JSONField) on that join.
            recs_qs = Recommendation.objects.select_related("analysis_run").defer(
                "analysis_run__llm_logs", "analysis_run__onboarding_prompts"
            )
            run = (
                AnalysisRun.objects.select_related("brand_visibility", "organization")
                .prefetch_related(
                    "page_scores",
                    "competitors",
                    Prefetch("recommendations", queryset=recs_qs),
                    "ai_probes",
                )
                .get(pk=run_id)
            )
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Analysis run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AnalysisRunDetailSerializer(run)
        return Response(serializer.data)


class AnalysisRunStatusView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]  # No throttling — this is a polling endpoint

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
    throttle_classes = [ExpensiveThrottle]

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
            import io

            from django.template.loader import render_to_string
            from xhtml2pdf import pisa

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
                    main_page_pillars.append(
                        {
                            "label": label,
                            "score": s,
                            "remainder": 100.0 - s,
                        }
                    )

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
            response["Content-Disposition"] = f'attachment; filename="geo-analysis-{run.id}.pdf"'
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
            user_email=email, defaults={"user_email": email}
        )

        serializer = UserGamificationSerializer(gamification)
        return Response(serializer.data)


class ActionTemplatesView(APIView):
    """Get available action templates"""

    permission_classes = [AllowAny]

    def get(self, request):
        templates = [{**template, "action_type": key} for key, template in ACTION_TEMPLATES.items()]
        return Response(templates)


class AchievementsView(APIView):
    """Get all possible achievements"""

    permission_classes = [AllowAny]

    def get(self, request):
        achievements = [{**info, "code": key} for key, info in ACHIEVEMENTS_INFO.items()]
        return Response(achievements)


class UserActionListView(APIView):
    """List user's actions"""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]  # used by sidebar/actions dashboard refreshes

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
            user_email=email, defaults={"user_email": email}
        )

        # Determine points value
        points = ACTION_TEMPLATES.get(data["action_type"], {}).get("points", 10)

        # Get related objects if provided
        recommendation = None
        if data.get("recommendation_id"):
            try:
                recommendation = Recommendation.objects.get(pk=data["recommendation_id"])
            except Recommendation.DoesNotExist:
                pass

        analysis_run = None
        if data.get("analysis_run_id"):
            try:
                analysis_run = AnalysisRun.objects.get(pk=data["analysis_run_id"])
            except AnalysisRun.DoesNotExist:
                pass

        # Create the action
        action = UserAction.objects.create(
            user_email=email,
            analysis_run=analysis_run,
            recommendation=recommendation,
            action_type=data["action_type"],
            title=data.get(
                "title", ACTION_TEMPLATES.get(data["action_type"], {}).get("title", "Custom Action")
            ),
            description=data.get("description", ""),
            points_value=points,
            score_before=data.get("score_before"),
            notes=data.get("notes", ""),
            status=UserAction.ActionStatus.PENDING,
        )

        return Response(UserActionSerializer(action).data, status=status.HTTP_201_CREATED)


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
            user_email=email, defaults={"user_email": email}
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
            user_email=email, defaults={"user_email": email}
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
    throttle_classes = [PollingThrottle]  # sidebar/actions open frequently

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

        overall_score = round(sum(item["score"] for item in files) / len(files), 1) if files else 0.0

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
    throttle_classes = [PollingThrottle]

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
            return Response(
                {"error": "Could not resolve automation config."}, status=status.HTTP_400_BAD_REQUEST
            )

        if config.is_active:
            _enqueue_daily_jobs(config, days_ahead=21)
            _process_due_blog_jobs(config, limit=10)

        return Response(
            {
                "config": BlogAutomationConfigSerializer(config).data,
            }
        )

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
            return Response(
                {"error": "Could not save automation config."}, status=status.HTTP_400_BAD_REQUEST
            )

        queued = _enqueue_daily_jobs(config, days_ahead=21) if config.is_active else 0

        return Response(
            {
                "message": "Automation settings saved.",
                "queued_jobs": queued,
                "config": BlogAutomationConfigSerializer(config).data,
            }
        )


class BlogAutomationCalendarView(APIView):
    """Calendar/list view for scheduled and published automated blogs."""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

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
    throttle_classes = [PollingThrottle]

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
    throttle_classes = [ExpensiveThrottle]

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
            if run_id
            else None
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
        # Always persist generated drafts so users don't lose them on refresh.
        draft_job_payload = None
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

        return Response(
            {
                "submenu_key": "ai-blog-automation",
                "submenu_name": "AI Blog Automation",
                "site_url": site_url,
                "source": source,
                "publish_provider": provider if integration else "none",
                "draft": draft,
                "draft_job": draft_job_payload,
            }
        )


class BlogAutomationPublishView(APIView):
    """Publish AI-generated draft to connected CMS."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

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
                return Response(
                    {"error": "Selected job has no draft content."}, status=status.HTTP_400_BAD_REQUEST
                )
            try:
                published = _publish_blog_job(job, publish_now=publish_now)
            except ValueError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            except Exception:
                logger.exception("Blog publish failed.")
                return Response(
                    {"error": "Unexpected error while publishing draft."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            return Response(
                {"message": "Blog job published.", "provider": job.provider, "published": published}
            )

        draft = request.data.get("draft") or {}
        title = str(draft.get("title", "")).strip()
        content_markdown = str(draft.get("content_markdown", "")).strip()
        if not title or not content_markdown:
            return Response(
                {"error": "Draft title and content_markdown are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        config = _get_or_create_blog_config(
            email=email,
            run_id=request.data.get("run_id"),
            analyzed_url=str(request.data.get("analyzed_url", "")),
            is_active=False,
        )
        if not config:
            return Response(
                {"error": "Could not prepare publish config."}, status=status.HTTP_400_BAD_REQUEST
            )

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
            return Response(
                {"error": "Unexpected error while publishing draft."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "message": "Blog draft processed successfully.",
                "provider": job.provider,
                "published": published,
            }
        )


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
            user_email=email, defaults={"user_email": email}
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
            user_email=email, defaults={"user_email": email}
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
            action_text = rec_data.get("action", "")
            priority = rec_data.get("priority", "medium")
            analysis_run_id = rec_data.get("analysis_run_id")

            # Only check for duplicates if we're scanning the SAME website again
            # Check by analysis_run_id + recommendation combination
            if rec_id and analysis_run_id:
                existing = UserAction.objects.filter(
                    recommendation_id=rec_id, analysis_run_id=analysis_run_id, user_email=email
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
    """POST /api/analyzer/generate-prompts/ — AI-generate brand-relevant prompts for onboarding.

    Gated by an onboarding token (see OnboardingStartView). This forces every
    fresh IP through a throttled (+ optionally Turnstile-gated) round-trip
    before they can spend a Gemini call here — defeats rotating-IP wallet
    drain that the per-IP DRF throttle alone can't stop.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request):
        client_ip = _client_ip(request)
        token = request.headers.get("X-Onboarding-Token", "")
        ok, reason = _verify_onboarding_token(token, client_ip)
        if not ok:
            logger.info("generate_prompts token_reject ip=%s reason=%s", client_ip, reason)
            return Response(
                {
                    "detail": "Onboarding token required. POST /api/analyzer/onboarding-start/ first.",
                    "reason": reason,
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

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
                    meta_desc = md["content"].strip() if md and md.get("content") else ""
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
            return Response(
                {
                    "prompts": [
                        f"What are the best alternatives to {brand_name}?",
                        f"Is {brand_name} worth using?",
                        f"Compare {brand_name} with competitors",
                        f"What do experts recommend instead of {brand_name}?",
                        f"Top tools similar to {brand_name}",
                    ]
                }
            )


# ============ Prompt Tracking Views ============


def _fire_and_save_prompt(track: PromptTrack, brand_name: str, brand_url: str):
    """Background worker: fires prompt across engines and saves PromptResult rows."""
    from django.db import close_old_connections

    from .pipeline.citations import competitor_hosts_for_run, host_of, persist_prompt_result
    from .pipeline.prompt_tracker import fire_prompt_across_engines

    close_old_connections()
    try:
        em = (track.analysis_run.email or "").strip()
        allowed = get_plan_limits(em)["engines"] if is_plan_limits_enforcement_enabled() and em else None
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
        from ._cache import invalidate_run_aggregates

        invalidate_run_aggregates(track.analysis_run.slug)
    except Exception as exc:
        logger.warning("PromptTrack #%d fire failed: %s", track.pk, exc)


class PromptListCreateView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        tracks = (
            run.prompt_tracks.filter(deleted_at__isnull=True)
            .select_related("analysis_run")
            # AnalysisRun.llm_logs / onboarding_prompts are large JSONField
            # blobs we don't need here — defer them to keep the join cheap.
            .defer("analysis_run__llm_logs", "analysis_run__onboarding_prompts")
            .prefetch_related("results", "results__citations")
            .order_by("-score", "-created_at")
        )
        serializer = PromptTrackSerializer(tracks, many=True)
        return Response(serializer.data)

    def post(self, request, slug):
        import threading

        from django.shortcuts import get_object_or_404

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
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug, track_id):
        import threading

        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, pk=track_id, analysis_run=run, deleted_at__isnull=True)

        brand_name = run.brand_name or run.url
        brand_url = run.url

        def _do():
            from django.db import close_old_connections

            from .pipeline.prompt_tracker import recheck_track

            close_old_connections()
            recheck_track(track, brand_name, brand_url)

        threading.Thread(target=_do, daemon=True).start()
        return Response({"status": "rechecking"}, status=status.HTTP_202_ACCEPTED)


class PromptBacklinksView(APIView):
    """GET /runs/s/<slug>/prompts/<track_id>/backlinks/ — Citation Authority panel.

    Thin HTTP layer — delegates all work to ``BacklinkAuthorityService``.
    Translates ``ProviderNotConfigured`` into a structured 503 the frontend
    can recognize and surface as "Backlink provider not configured".
    """

    permission_classes = [AllowAny]
    throttle_classes = [DataForSEOThrottle]

    def get(self, request, slug, track_id):
        from django.shortcuts import get_object_or_404

        from .services.backlink_authority import (
            BacklinkAuthorityService,
            ProviderNotConfigured,
        )

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(
            PromptTrack,
            pk=track_id,
            analysis_run=run,
            deleted_at__isnull=True,
        )

        try:
            payload = BacklinkAuthorityService(track=track).build()
        except ProviderNotConfigured as exc:
            return Response(
                {"detail": str(exc), "code": "dataforseo_not_configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(payload)


class PromptOpportunitiesView(APIView):
    """GET / POST /runs/s/<slug>/prompts/<track_id>/opportunities/

    Thin HTTP layer — delegates to ``OpportunityService`` for all logic.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug, track_id):
        service = _opportunity_service(slug, track_id)
        return Response(service.list())

    def post(self, request, slug, track_id):
        service = _opportunity_service(slug, track_id)
        from .services.opportunities import OpportunityServiceError

        try:
            return Response(service.regenerate())
        except OpportunityServiceError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )


class PromptOpportunityDetailView(APIView):
    """PATCH / DELETE /runs/s/<slug>/prompts/<track_id>/opportunities/<opp_id>/"""

    permission_classes = [AllowAny]

    def patch(self, request, slug, track_id, opp_id):
        service = _opportunity_service(slug, track_id)
        from .services.opportunities import OpportunityServiceError

        try:
            payload = service.update_status(
                opp_id,
                new_status=request.data.get("status"),
                live_url=request.data.get("live_url"),
            )
        except OpportunityServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)

    def delete(self, request, slug, track_id, opp_id):
        service = _opportunity_service(slug, track_id)
        from .services.opportunities import OpportunityServiceError

        try:
            service.delete(opp_id)
        except OpportunityServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _opportunity_service(slug: str, track_id: int):
    """Resolve the run+track and return an OpportunityService bound to it."""
    from django.shortcuts import get_object_or_404

    from .services.opportunities import OpportunityService

    run = get_object_or_404(AnalysisRun, slug=slug)
    track = get_object_or_404(
        PromptTrack,
        pk=track_id,
        analysis_run=run,
        deleted_at__isnull=True,
    )
    return OpportunityService(track=track)


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
        track = get_object_or_404(PromptTrack, pk=track_id, analysis_run=run, deleted_at__isnull=True)
        track.deleted_at = timezone.now()
        track.save(update_fields=["deleted_at"])
        from ._cache import invalidate_run_aggregates

        invalidate_run_aggregates(slug)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RecheckAllPromptsView(APIView):
    """POST /runs/s/<slug>/recheck-all/ — re-fire every prompt for this run."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        import threading

        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        tracks = list(run.prompt_tracks.filter(deleted_at__isnull=True))

        if not tracks:
            return Response({"status": "no_tracks", "count": 0})

        brand_name = run.brand_name or run.url
        brand_url = run.url

        def _do_all():
            from django.db import close_old_connections

            from .pipeline.prompt_tracker import recheck_track

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
        from django.db.models import Count, Q
        from django.shortcuts import get_object_or_404

        from ._cache import cached_or_compute

        run = get_object_or_404(AnalysisRun, slug=slug)

        def _compute():
            em = (run.email or "").strip()
            valid_engine_keys = {e[0] for e in PromptResult.Engine.choices}
            if is_plan_limits_enforcement_enabled() and em:
                engines = [e for e in get_plan_limits(em)["engines"] if e in valid_engine_keys]
            else:
                engines = [e[0] for e in PromptResult.Engine.choices]
            # One aggregation query per engine -> engines * 2 round trips. Cached
            # for 10 min so the dashboard's first paint amortizes the work.
            data = []
            for engine in engines:
                qs = PromptResult.objects.filter(prompt_track__analysis_run=run, engine=engine)
                agg = qs.aggregate(
                    total=Count("id"),
                    mentioned=Count("id", filter=Q(brand_mentioned=True)),
                )
                total = agg["total"] or 0
                mentioned = agg["mentioned"] or 0
                sov_pct = round((mentioned / total * 100), 1) if total > 0 else 0.0
                data.append({"engine": engine, "total": total, "mentioned": mentioned, "sov_pct": sov_pct})
            return data

        data = cached_or_compute(f"sov:{slug}", 600, _compute)
        return Response(ShareOfVoiceSerializer(data, many=True).data)


class CitationTrendView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.db.models import Count, Q
        from django.db.models.functions import TruncWeek
        from django.shortcuts import get_object_or_404

        from ._cache import cached_or_compute

        run = get_object_or_404(AnalysisRun, slug=slug)

        def _compute():
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
                base.annotate(week_start=TruncWeek("checked_at"))
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
                data.append(
                    {
                        # Stored as ISO string so Redis serialization round-trips
                        # cleanly (date objects don't pickle through JSON cache).
                        "week_start": row["week_start"].date().isoformat() if row["week_start"] else None,
                        "engine": row["engine"],
                        "rate_pct": round((mentioned / total * 100), 1) if total > 0 else 0.0,
                    }
                )
            return data

        data = cached_or_compute(f"trend:{slug}", 300, _compute)
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
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from django.db.models import Count, Exists, OuterRef, Q
        from django.shortcuts import get_object_or_404

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
                per_engine.append(
                    {
                        "engine": row["engine"],
                        "total": e_total,
                        "mentioned": row["mentioned"] or 0,
                        "recommended": e_rec,
                        "cited": row["cited"] or 0,
                        "recommendation_pct": round((e_rec / e_total * 100), 1) if e_total > 0 else 0.0,
                    }
                )

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
        from collections import defaultdict

        from django.db.models import Count, Q
        from django.shortcuts import get_object_or_404

        from ._cache import cached_or_compute
        from .models import PromptCitation

        run = get_object_or_404(AnalysisRun, slug=slug)

        def _compute():
            qs = PromptCitation.objects.filter(
                prompt_result__prompt_track__analysis_run=run,
                prompt_result__prompt_track__deleted_at__isnull=True,
            ).exclude(domain="")

            # One aggregate for the three count totals — was 3 round trips.
            counts = qs.aggregate(
                total=Count("id"),
                brand=Count("id", filter=Q(is_brand=True)),
                rival=Count("id", filter=Q(is_competitor=True)),
            )

            # Domain roll-up
            domain_rows = list(qs.values("domain").annotate(total=Count("id")).order_by("-total")[:40])
            top_domains = [r["domain"] for r in domain_rows]

            # Flags per domain (is_brand / is_competitor) — restricted to the
            # top-N we'll actually return so we don't iterate all citations.
            flag_map: dict[str, dict] = {}
            if top_domains:
                for c in qs.filter(domain__in=top_domains).values("domain", "is_brand", "is_competitor"):
                    f = flag_map.setdefault(c["domain"], {"is_brand": False, "is_competitor": False})
                    if c["is_brand"]:
                        f["is_brand"] = True
                    if c["is_competitor"]:
                        f["is_competitor"] = True

            # Per-engine breakdown for top domains
            by_engine: dict[str, dict] = defaultdict(dict)
            if top_domains:
                engine_rows = (
                    qs.filter(domain__in=top_domains)
                    .values("domain", "prompt_result__engine")
                    .annotate(total=Count("id"))
                )
                for r in engine_rows:
                    by_engine[r["domain"]][r["prompt_result__engine"]] = r["total"]

            # Sample URL for each top domain
            sample_map: dict[str, str] = {}
            if top_domains:
                for c in qs.filter(domain__in=top_domains).values("domain", "url")[:500]:
                    sample_map.setdefault(c["domain"], c["url"])

            domains = []
            for row in domain_rows:
                d = row["domain"]
                flags = flag_map.get(d, {"is_brand": False, "is_competitor": False})
                domains.append(
                    {
                        "domain": d,
                        "total": row["total"],
                        "is_brand": flags["is_brand"],
                        "is_competitor": flags["is_competitor"],
                        "by_engine": dict(by_engine.get(d, {})),
                        "sample_url": sample_map.get(d, ""),
                    }
                )

            your_pages = list(
                qs.filter(is_brand=True)
                .values("url", "title")
                .annotate(mentions=Count("id"))
                .order_by("-mentions")[:10]
            )
            rival_pages = list(
                qs.filter(is_competitor=True)
                .values("url", "title", "domain")
                .annotate(mentions=Count("id"))
                .order_by("-mentions")[:10]
            )

            return {
                "total_citations": counts["total"] or 0,
                "brand_citations": counts["brand"] or 0,
                "competitor_citations": counts["rival"] or 0,
                "domains": domains,
                "your_pages": your_pages,
                "rival_pages": rival_pages,
            }

        return Response(cached_or_compute(f"cite:{slug}", 600, _compute))


class BrandKitView(APIView):
    """GET/POST /api/analyzer/runs/s/<slug>/brand-kit/

    GET:  return the cached submission kit; auto-generate on first call.
    POST: force a fresh regeneration (drops cache, re-runs the LLM).

    The kit is the user's "click-to-copy" payload for filling out directory
    and review-site submission forms. It's a thin wrapper around
    ``services.brand_kit.get_or_generate``.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .services.brand_kit import BrandKitError, get_or_generate

        run = get_object_or_404(AnalysisRun, slug=slug)
        try:
            return Response({"kit": get_or_generate(run)})
        except BrandKitError as exc:
            return Response(
                {"detail": str(exc), "code": "kit_generation_failed"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .services.brand_kit import BrandKitError, get_or_generate

        run = get_object_or_404(AnalysisRun, slug=slug)
        try:
            return Response({"kit": get_or_generate(run, force=True)})
        except BrandKitError as exc:
            return Response(
                {"detail": str(exc), "code": "kit_generation_failed"},
                status=status.HTTP_502_BAD_GATEWAY,
            )


class DomainAnalyticsView(APIView):
    """GET/POST /api/analyzer/runs/s/<slug>/domain-analytics/

    SEMrush-style real-world signals (estimated organic traffic, top keywords,
    top pages) sourced from DataForSEO Labs. No GA connection required.

    GET:  return the cached snapshot, auto-fetch on first call.
    POST: force a fresh fetch (3 DataForSEO API calls, ~$0.015 / refresh).
    """

    permission_classes = [AllowAny]
    throttle_classes = [DataForSEOThrottle]

    def _respond(self, run, *, force: bool):
        from apps.integrations.services.dataforseo import DataForSEOError, DataForSEONotConfigured

        from .services.domain_analytics import DomainAnalyticsError, get_or_generate

        try:
            return Response(get_or_generate(run, force=force))
        except DataForSEONotConfigured:
            return Response(
                {
                    "detail": "DataForSEO is not configured.",
                    "code": "dataforseo_not_configured",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except DataForSEOError as exc:
            # Upstream API failure (out of credits, billing issue, 4xx/5xx).
            msg = str(exc)
            code = "dataforseo_billing" if "402" in msg else "dataforseo_upstream"
            return Response(
                {"detail": msg, "code": code},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except DomainAnalyticsError as exc:
            return Response(
                {"detail": str(exc), "code": "domain_analytics_failed"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        return self._respond(run, force=False)

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        return self._respond(run, force=True)


class CompetitorListCreateView(APIView):
    """GET/POST /api/analyzer/runs/s/<slug>/competitors/"""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

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
            qs.order_by("updated_at").values("id", "created_at", "updated_at", "composite_score", "slug")
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
            return Response(
                {"error": "frequency must be once/weekly/monthly."}, status=status.HTTP_400_BAD_REQUEST
            )

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
                return Response(
                    {"error": "run_at must be an ISO datetime."}, status=status.HTTP_400_BAD_REQUEST
                )
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            if parsed <= timezone.now():
                return Response(
                    {"error": "run_at must be in the future."}, status=status.HTTP_400_BAD_REQUEST
                )
            next_run_at = parsed
        else:
            if frequency == "once":
                return Response(
                    {"error": "run_at is required when frequency=once."}, status=status.HTTP_400_BAD_REQUEST
                )
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

        return Response(
            ScheduledAnalysisSerializer(schedule).data,
            status=status.HTTP_200_OK if not created else status.HTTP_201_CREATED,
        )


# ── Auto-Fix ──────────────────────────────────────────────────────────────


class AutoFixView(APIView):
    """GET/POST /api/analyzer/runs/s/<slug>/auto-fix/"""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

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
                AutoFixJob.objects.filter(analysis_run__organization=run.organization, status="success")
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

        if not recommendation_ids or not email:
            return Response(
                {"error": "recommendation_ids and email are required."}, status=status.HTTP_400_BAD_REQUEST
            )

        # Match Shopify vs WordPress to the analyzed URL (org may have both connected)
        org = run.organization
        if not org:
            return Response(
                {"error": "No organization linked to this run."}, status=status.HTTP_400_BAD_REQUEST
            )

        from .integration_resolve import resolve_store_integration_for_run

        integration = resolve_store_integration_for_run(org, run.url or "")

        if not integration:
            return Response(
                {"error": "No WordPress or Shopify integration connected. Connect one first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        recommendations = Recommendation.objects.filter(id__in=recommendation_ids, analysis_run=run)

        results = apply_fixes(run, integration, list(recommendations))
        return Response(results)


class AutoFixPreviewView(APIView):
    """POST /api/analyzer/runs/s/<slug>/auto-fix/preview/ — generate fix preview without applying.

    Persists the preview as an AutoFixJob with status='preview'. If a preview
    already exists for the same (run, recommendation), returns it without
    re-running the LLM. Pass force=true to regenerate.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .auto_fix import generate_fix_preview
        from .models import AutoFixJob

        run = get_object_or_404(AnalysisRun, slug=slug)
        rec_id = request.data.get("recommendation_id")
        email = request.data.get("email", "").lower().strip()
        force = bool(request.data.get("force"))

        if not rec_id or not email:
            return Response(
                {"error": "recommendation_id and email required."}, status=status.HTTP_400_BAD_REQUEST
            )

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

        if not force:
            cached = (
                AutoFixJob.objects.filter(
                    analysis_run=run,
                    recommendation=rec,
                    status=AutoFixJob.Status.PREVIEW,
                )
                .order_by("-created_at")
                .first()
            )
            if cached and cached.response_data:
                return Response({**cached.response_data, "cached": True})

        preview = generate_fix_preview(run, integration, rec)
        try:
            AutoFixJob.objects.update_or_create(
                analysis_run=run,
                recommendation=rec,
                status=AutoFixJob.Status.PREVIEW,
                defaults={
                    "integration": integration,
                    "fix_type": preview.get("fix_type", "content"),
                    "response_data": preview,
                },
            )
        except Exception:
            logger.exception("Failed to persist AutoFixJob preview (run=%s rec=%s)", run.id, rec.id)
        return Response({**preview, "cached": False})


class AutoFixApproveView(APIView):
    """POST /api/analyzer/runs/s/<slug>/auto-fix/approve/ — apply a previewed fix via plugin."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

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
        err_msg = result.get("message", "") if raw_status in ("failed", "error", "skipped") else ""
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
    throttle_classes = [ExpensiveThrottle]

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
                error_message="" if st == "verified" else (result.get("message") or "")[:500],
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
    """
    GET  /api/analyzer/runs/s/<slug>/chat/ — return persisted chat history.
    POST /api/analyzer/runs/s/<slug>/chat/ — send a message; reply persists.

    The backend is the source of truth for chat history — clients no longer
    need to round-trip the conversation. Sending `history` in the body is
    ignored unless the run has zero saved messages (legacy migration aid).
    """

    permission_classes = [AllowAny]
    throttle_classes = [AiChatThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .models import ChatMessage

        run = get_object_or_404(AnalysisRun, slug=slug)
        msgs = ChatMessage.objects.filter(analysis_run=run).order_by("created_at")
        return Response(
            {
                "messages": [
                    {
                        "role": m.role,
                        "content": m.content,
                        "created_at": m.created_at.isoformat(),
                    }
                    for m in msgs
                ]
            }
        )

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .models import ChatMessage

        run = get_object_or_404(AnalysisRun, slug=slug)
        message = request.data.get("message", "").strip()
        # Pull persisted history; fall back to client-sent on first ever message.
        persisted = list(ChatMessage.objects.filter(analysis_run=run).order_by("created_at"))
        if persisted:
            history = [{"role": m.role, "content": m.content} for m in persisted]
        else:
            history = request.data.get("history", []) or []

        if not message:
            return Response({"error": "message required"}, status=status.HTTP_400_BAD_REQUEST)

        # Build context from THIS run's stored data only — no re-crawling
        page_score = run.page_scores.filter(url=run.url).first()
        all_page_scores = list(
            run.page_scores.all().values(
                "url", "content_score", "schema_score", "eeat_score", "technical_score"
            )
        )
        recs = list(run.recommendations.values_list("title", "priority", "pillar", "description")[:15])

        # Extract brand info from stored analysis details
        word_count = 0
        site_discovery = {}

        if page_score and page_score.content_details:
            checks = page_score.content_details.get("checks", {})
            coverage = checks.get("coverage_depth", {})
            word_count = checks.get("word_count", coverage.get("word_count", 0))
            site_discovery = checks.get("site_discovery", {})

        # Build context
        brand = run.brand_name or run.url
        context_parts = [
            f"Brand: {brand}",
            f"URL: {run.url}",
            f"Word count on homepage: {word_count}",
        ]

        if site_discovery:
            context_parts.append(
                f"Site structure: {site_discovery.get('products', 0)} products, "
                f"{site_discovery.get('collections', 0)} collections, "
                f"{site_discovery.get('pages', 0)} pages, "
                f"{site_discovery.get('blog_posts', 0)} blog posts"
            )

        context_parts.append("\n--- EXACT SCORES (from this analysis) ---")
        context_parts.append(f"Overall GEO Score: {round(run.composite_score, 1)}/100")

        if page_score:
            context_parts.extend(
                [
                    f"Technical: {round(page_score.technical_score, 1)}/100",
                    f"Schema: {round(page_score.schema_score, 1)}/100",
                    f"Content: {round(page_score.content_score, 1)}/100",
                    f"E-E-A-T: {round(page_score.eeat_score, 1)}/100",
                    f"Entity: {round(page_score.entity_score, 1)}/100",
                    f"AI Visibility: {round(page_score.ai_visibility_score, 1)}/100",
                ]
            )

            # Add detail breakdowns if available
            if page_score.content_details and page_score.content_details.get("checks"):
                cc = page_score.content_details["checks"]
                context_parts.append(
                    f"\nContent breakdown: intent={cc.get('intent_score', '?')}, "
                    f"coverage={cc.get('coverage_score', '?')}, "
                    f"density={cc.get('density_score', '?')}, "
                    f"structure={cc.get('structure_score', '?')}"
                )

            if page_score.eeat_details and page_score.eeat_details.get("checks"):
                ec = page_score.eeat_details["checks"]
                context_parts.append(
                    f"E-E-A-T breakdown: identity={ec.get('identity_score', '?')}, "
                    f"evidence={ec.get('evidence_score', '?')}, "
                    f"experience={ec.get('experience_score', '?')}, "
                    f"trust={ec.get('trust_score', '?')}"
                )

            if page_score.technical_details and page_score.technical_details.get("checks"):
                tc = page_score.technical_details["checks"]
                context_parts.append(
                    f"Technical breakdown: infra={tc.get('infra_score', '?')}, "
                    f"perf={tc.get('perf_score', '?')}, "
                    f"crawl={tc.get('crawl_score', '?')}, "
                    f"ai_read={tc.get('ai_read_score', '?')}, "
                    f"struct={tc.get('struct_score', '?')}"
                )

        if len(all_page_scores) > 1:
            context_parts.append(f"\nPages analyzed: {len(all_page_scores)}")
            for ps in all_page_scores[:5]:
                context_parts.append(
                    f"  - {ps['url']}: content={round(ps['content_score'], 1)}, "
                    f"schema={round(ps['schema_score'], 1)}, eeat={round(ps['eeat_score'], 1)}"
                )

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

        # Persist the user message before invoking the LLM so it survives errors.
        ChatMessage.objects.create(analysis_run=run, role=ChatMessage.Role.USER, content=message)

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

            reply_text = reply.strip()
            ChatMessage.objects.create(analysis_run=run, role=ChatMessage.Role.ASSISTANT, content=reply_text)
            return Response({"reply": reply_text})
        except Exception as exc:
            logger.warning("AI Chat failed: %s", exc)
            fallback = "Sorry, I couldn't process that right now. Please try again."
            ChatMessage.objects.create(analysis_run=run, role=ChatMessage.Role.ASSISTANT, content=fallback)
            return Response({"reply": fallback})


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
    throttle_classes = [ExpensiveThrottle]

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
    throttle_classes = [AuditStartThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .models import SitemapAudit
        from .pipeline.sitemap_audit import HARD_URL_CAP, run_sitemap_audit
        from .serializers import SitemapAuditSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        audit = SitemapAudit.objects.create(
            analysis_run=run,
            status=SitemapAudit.Status.QUEUED,
            crawl_limit=HARD_URL_CAP,
        )

        from ._thread_safety import run_in_background_with_status

        run_in_background_with_status(
            model_cls=SitemapAudit,
            instance_id=audit.id,
            status_field="status",
            failure_value=SitemapAudit.Status.FAILED,
            work=lambda: run_sitemap_audit(audit.id),
            log_label="run_sitemap_audit",
        )

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
        from .serializers import SitemapAuditPageSerializer, SitemapAuditSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        audit = SitemapAudit.objects.filter(analysis_run=run).order_by("-created_at").first()
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
        rows = list(qs[start_idx : start_idx + page_size])

        return Response(
            {
                "audit": SitemapAuditSerializer(audit).data,
                "pages": SitemapAuditPageSerializer(rows, many=True).data,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )


class AgentLogView(APIView):
    """GET /runs/s/<slug>/agent-log/  — stub; returns empty entries + integration slots."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .models import AgentLogEntry
        from .serializers import AgentLogEntrySerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        entries = AgentLogEntry.objects.filter(analysis_run=run).order_by("-ts")[:100]
        return Response(
            {
                "entries": AgentLogEntrySerializer(entries, many=True).data,
                "integrations": [
                    {
                        "name": "Cloudflare Logpush",
                        "key": "cloudflare",
                        "connected": False,
                        "status": "coming_soon",
                    },
                    {
                        "name": "Vercel Edge Logs",
                        "key": "vercel",
                        "connected": False,
                        "status": "coming_soon",
                    },
                ],
            }
        )


# ============================================================================
# Schema Watchtower
# ============================================================================


class SchemaWatchStartView(APIView):
    """POST /runs/s/<slug>/schema-watch/  — kick off a schema validation run."""

    permission_classes = [AllowAny]
    throttle_classes = [AuditStartThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .models import SchemaWatch
        from .pipeline.schema_watch import run_schema_watch
        from .serializers import SchemaWatchSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        watch = SchemaWatch.objects.create(
            analysis_run=run,
            status=SchemaWatch.Status.QUEUED,
        )

        from ._thread_safety import run_in_background_with_status

        run_in_background_with_status(
            model_cls=SchemaWatch,
            instance_id=watch.id,
            status_field="status",
            failure_value=SchemaWatch.Status.FAILED,
            work=lambda: run_schema_watch(watch.id),
            log_label="run_schema_watch",
        )

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
        from .serializers import SchemaWatchPageSerializer, SchemaWatchSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        watch = SchemaWatch.objects.filter(analysis_run=run).order_by("-created_at").first()
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
        rows = list(qs[start_idx : start_idx + page_size])

        return Response(
            {
                "watch": SchemaWatchSerializer(watch).data,
                "pages": SchemaWatchPageSerializer(rows, many=True).data,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )


class RankAuditStartView(APIView):
    """POST /runs/s/<slug>/rank/start/ — kick off an async rank audit."""

    permission_classes = [AllowAny]
    throttle_classes = [AuditStartThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .models import RankAudit
        from .pipeline.rank_tracker import run_rank_audit
        from .serializers import RankAuditSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)

        already = RankAudit.objects.filter(
            analysis_run=run, status__in=[RankAudit.Status.QUEUED, RankAudit.Status.RUNNING]
        ).first()
        if already is not None:
            return Response(
                {
                    "detail": "An audit is already running for this run.",
                    "audit": RankAuditSerializer(already).data,
                },
                status=status.HTTP_409_CONFLICT,
            )

        audit = RankAudit.objects.create(
            analysis_run=run,
            status=RankAudit.Status.QUEUED,
        )

        from ._thread_safety import run_in_background_with_status

        run_in_background_with_status(
            model_cls=RankAudit,
            instance_id=audit.id,
            status_field="status",
            failure_value=RankAudit.Status.FAILED,
            work=lambda: run_rank_audit(audit.id),
            log_label="run_rank_audit",
        )

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
        from .serializers import RankAuditSerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        audit = RankAudit.objects.filter(analysis_run=run).order_by("-created_at").first()
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

        return Response(
            {
                "audit": RankAuditSerializer(audit).data,
                "queries": serialized,
            }
        )


class RankAuditRefreshQueryView(APIView):
    """POST /runs/s/<slug>/rank/query/<query_id>/refresh/ — re-fetch one query across all surfaces."""

    permission_classes = [AllowAny]
    throttle_classes = [AuditStartThrottle]

    def post(self, request, slug, query_id):
        from django.shortcuts import get_object_or_404

        from .models import RankAudit, RankQuery, RankResult
        from .pipeline.rank_tracker import audit_query
        from .serializers import RankQuerySerializer

        run = get_object_or_404(AnalysisRun, slug=slug)
        audit = RankAudit.objects.filter(analysis_run=run).order_by("-created_at").first()
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
            competitor_names = [c for c in run.competitors.values_list("name", flat=True) if c]
        except Exception:
            competitor_names = []
        gl = _derive_geo(run).get("gl") or ""

        from ._thread_safety import run_in_background_with_status
        from .models import RankQuery as _RQ

        def _refresh():
            q = _RQ.objects.get(pk=query.id)
            audit_query(q, brand_names, competitor_names, brand_domain=brand_domain, gl=gl)

        run_in_background_with_status(
            model_cls=_RQ,
            instance_id=query.id,
            status_field="status",
            failure_value=_RQ.Status.FAILED,
            work=_refresh,
            log_label="refresh_rank_query",
        )

        return Response(RankQuerySerializer(query).data, status=status.HTTP_202_ACCEPTED)


class PromptRankView(APIView):
    """
    GET/POST /runs/s/<slug>/prompts/<int:track_id>/rank/

    Returns top-3 web ranking (Google / Reddit / Quora) for the tracked
    prompt's text. Lazily creates a RankQuery on the latest audit and runs
    only the web-surface fetchers synchronously so the prompt expands with
    real data the first time it's opened.
    """

    permission_classes = [AllowAny]
    throttle_classes = [DataForSEOThrottle]

    def _serialize(self, query):
        results = list(
            query.results.filter(surface__in=["google", "reddit", "quora"]).order_by("surface", "position")
        )
        return {
            "id": query.id,
            "prompt_text": query.prompt_text,
            "rank": query.rank,
            "brand_mention_count": query.brand_mention_count,
            "status": query.status,
            "error_message": query.error_message,
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
                for r in results
            ],
        }

    def _ensure_audit(self, run):
        from .models import RankAudit

        audit = RankAudit.objects.filter(analysis_run=run).order_by("-created_at").first()
        if audit is None:
            audit = RankAudit.objects.create(
                analysis_run=run,
                status=RankAudit.Status.COMPLETE,
            )
        return audit

    def _get_or_create_query(self, audit, prompt_text):
        from django.db.models import Max

        from .models import RankQuery

        query = RankQuery.objects.filter(audit=audit, prompt_text=prompt_text).order_by("-id").first()
        if query is None:
            next_rank = (RankQuery.objects.filter(audit=audit).aggregate(m=Max("rank"))["m"] or 0) + 1
            query = RankQuery.objects.create(
                audit=audit,
                prompt_text=prompt_text,
                rank=next_rank,
                status=RankQuery.Status.QUEUED,
            )
        return query

    def _run_web_fetch(self, query, run):
        from urllib.parse import urlparse as _urlparse

        from .models import RankQuery, RankResult
        from .pipeline.rank_tracker import (
            _derive_geo,
            compute_sentiment,
            detect_brand_mentions,
            fetch_quora,
            fetch_reddit,
            fetch_serper,
        )

        brand_names = [n for n in (run.brand_name,) if n]
        try:
            brand_domain = _urlparse(run.url or "").netloc.lower().replace("www.", "")
        except Exception:
            brand_domain = ""
        if brand_domain:
            brand_names.append(brand_domain)
        try:
            competitor_names = [c for c in run.competitors.values_list("name", flat=True) if c]
        except Exception:
            competitor_names = []
        gl = _derive_geo(run).get("gl") or ""

        # Clear any prior web-surface results so re-runs don't pile up.
        RankResult.objects.filter(query=query, surface__in=["google", "reddit", "quora"]).delete()

        fetchers = (
            ("google", lambda q: fetch_serper(q, gl=gl)),
            ("reddit", lambda q: fetch_reddit(q, gl=gl)),
            ("quora", lambda q: fetch_quora(q, gl=gl)),
        )

        to_create = []
        brand_hits = 0
        for surface, fn in fetchers:
            try:
                rows = fn(query.prompt_text) or []
            except Exception as exc:
                logger.warning(
                    "PromptRankView surface=%s prompt=%r error: %s",
                    surface,
                    query.prompt_text[:80],
                    exc,
                )
                rows = []
            # Keep only the top 3 per surface — that's all we display.
            rows = rows[:3]
            for row in rows:
                snippet = row.get("snippet", "") or ""
                is_brand, comps = detect_brand_mentions(
                    row.get("title", ""),
                    snippet,
                    brand_names,
                    competitor_names,
                    result_domain=row.get("domain", ""),
                    result_url=row.get("url", ""),
                    brand_domain=brand_domain,
                )
                if is_brand:
                    brand_hits += 1
                sentiment = compute_sentiment(f"{row.get('title') or ''} {snippet}")
                to_create.append(
                    RankResult(
                        query=query,
                        surface=surface,
                        position=int(row.get("position") or 0),
                        url=(row.get("url") or "")[:2048],
                        domain=(row.get("domain") or "")[:255],
                        title=(row.get("title") or "")[:300],
                        snippet=(row.get("snippet") or "")[:4000],
                        engine="",
                        response_text="",
                        sentiment=sentiment,
                        is_brand_mentioned=is_brand,
                        competitors_mentioned=comps,
                        upvotes=row.get("upvotes"),
                        subreddit=(row.get("subreddit") or "")[:120],
                    )
                )

        if to_create:
            RankResult.objects.bulk_create(to_create)

        query.brand_mention_count = brand_hits
        query.status = RankQuery.Status.DONE
        query.error_message = ""
        query.save(update_fields=["brand_mention_count", "status", "error_message"])

    def _resolve(self, slug, track_id, *, force_refresh=False):
        from django.shortcuts import get_object_or_404

        from .models import PromptTrack

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, id=track_id, analysis_run=run)

        prompt_text = (track.prompt_text or "").strip()
        if not prompt_text:
            return None, Response(
                {"detail": "Prompt has no text."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audit = self._ensure_audit(run)
        query = self._get_or_create_query(audit, prompt_text)

        has_web_results = query.results.filter(surface__in=["google", "reddit", "quora"]).exists()

        if force_refresh or not has_web_results:
            try:
                self._run_web_fetch(query, run)
            except Exception as exc:
                logger.exception("PromptRankView fetch failed: %s", exc)
                query.status = "failed"
                query.error_message = str(exc)[:500]
                query.save(update_fields=["status", "error_message"])

        # Re-load to reflect any saved results.
        query.refresh_from_db()
        return query, None

    def get(self, request, slug, track_id):
        query, err = self._resolve(slug, track_id, force_refresh=False)
        if err is not None:
            return err
        return Response(self._serialize(query))

    def post(self, request, slug, track_id):
        force = str(request.data.get("refresh", "")).lower() in ("1", "true", "yes")
        query, err = self._resolve(slug, track_id, force_refresh=force)
        if err is not None:
            return err
        return Response(self._serialize(query))


# ─────────────────────────────────────────────────────────────────────────────
# Backlink marketplace
# ─────────────────────────────────────────────────────────────────────────────


def _serialize_product(p):
    return {
        "id": p.id,
        "provider": p.provider.slug,
        "provider_name": p.provider.display_name,
        "sku": p.sku,
        "domain": p.domain,
        "title": p.title,
        "link_type": p.link_type,
        "domain_authority": p.domain_authority,
        "domain_rank": p.domain_rank,
        "monthly_traffic": p.monthly_traffic,
        "niche_tags": p.niche_tags or [],
        "language": p.language,
        "country": p.country,
        "do_follow": p.do_follow,
        "price_cents": p.retail_price_cents,
        "currency": p.currency,
        "lead_time_days": p.lead_time_days,
    }


def _serialize_order(o):
    return {
        "id": o.id,
        "status": o.status,
        "provider": o.provider.slug,
        "provider_name": o.provider.display_name,
        "domain": o.product.domain,
        "title": o.product.title,
        "target_url": o.target_url,
        "anchor_text": o.anchor_text,
        "price_cents": o.price_cents,
        "currency": o.currency,
        "proof_url": o.proof_url,
        "error_message": o.error_message,
        "prompt_track_id": o.prompt_track_id,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "ordered_at": o.ordered_at.isoformat() if o.ordered_at else None,
        "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
    }


class BacklinkCatalogView(APIView):
    """
    GET /runs/s/<slug>/backlinks/catalog/

    Returns the cached catalog for all enabled providers. Refreshes from each
    provider on first call (when the cache is empty) so the UI never sees an
    empty list.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .models import BacklinkProduct, BacklinkProvider

        # We don't strictly need the run — catalog is global — but checking the
        # slug exists keeps the URL shape consistent with the rest of the API.
        get_object_or_404(AnalysisRun, slug=slug)

        # Idempotent — adds any newly-defined providers without touching existing rows.
        self._seed_default_providers()
        providers = list(BacklinkProvider.objects.filter(is_enabled=True))

        for provider in providers:
            if not provider.products.exists():
                self._refresh_provider_catalog(provider)

        # Optional filters: ?link_type=guest_post&min_da=70&niche=tech
        qs = BacklinkProduct.objects.select_related("provider").filter(provider__is_enabled=True)
        link_type = request.GET.get("link_type")
        if link_type:
            qs = qs.filter(link_type=link_type)
        try:
            min_da = int(request.GET.get("min_da") or 0)
        except (TypeError, ValueError):
            min_da = 0
        if min_da > 0:
            qs = qs.filter(domain_authority__gte=min_da)
        niche = (request.GET.get("niche") or "").strip().lower()
        if niche:
            qs = qs.filter(niche_tags__contains=[niche])

        return Response(
            {
                "providers": [{"slug": p.slug, "display_name": p.display_name} for p in providers],
                "products": [_serialize_product(p) for p in qs[:200]],
            }
        )

    @staticmethod
    def _seed_default_providers():
        from .models import BacklinkProvider

        BacklinkProvider.objects.get_or_create(
            slug="fatjoe",
            defaults={
                "display_name": "FATJOE",
                "homepage_url": "https://fatjoe.com",
                "is_enabled": True,
                "notes": "Reseller / white-label backlinks marketplace.",
            },
        )
        BacklinkProvider.objects.get_or_create(
            slug="budget_links",
            defaults={
                "display_name": "BudgetLinks",
                "homepage_url": "",
                "is_enabled": True,
                "notes": "Budget-tier reseller — sub-$50 placements, niche guest posts, profile citations.",
            },
        )

    @staticmethod
    def _refresh_provider_catalog(provider):
        """Pull current catalog from the provider and upsert into BacklinkProduct."""
        from apps.integrations.services.backlink_providers import get_client

        from .models import BacklinkProduct

        try:
            client = get_client(provider.slug)
            rows = client.list_products()
        except Exception as exc:
            logger.warning(
                "BacklinkCatalogView: failed to refresh %s catalog: %s",
                provider.slug,
                exc,
            )
            return

        for row in rows:
            BacklinkProduct.objects.update_or_create(
                provider=provider,
                sku=row.sku,
                defaults={
                    "domain": row.domain,
                    "title": row.title,
                    "link_type": row.link_type,
                    "domain_authority": row.domain_authority,
                    "domain_rank": row.domain_rank,
                    "monthly_traffic": row.monthly_traffic,
                    "niche_tags": row.niche_tags,
                    "language": row.language,
                    "country": row.country,
                    "do_follow": row.do_follow,
                    "wholesale_price_cents": row.wholesale_price_cents,
                    "retail_price_cents": row.retail_price_cents,
                    "currency": row.currency,
                    "lead_time_days": row.lead_time_days,
                    "extras": row.extras,
                },
            )


class BacklinkOrderListCreateView(APIView):
    """
    GET  /runs/s/<slug>/backlinks/orders/         — list orders for this run
    POST /runs/s/<slug>/backlinks/orders/         — place an order

    POST body:
      {
        "product_id": int,
        "target_url": str,
        "anchor_text": str,
        "track_id": int | null,    // optional, link the order to a prompt
        "notes": str | null,
        "user_email": str
      }
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .models import BacklinkOrder

        run = get_object_or_404(AnalysisRun, slug=slug)
        qs = (
            BacklinkOrder.objects.filter(analysis_run=run)
            .select_related("provider", "product")
            .order_by("-created_at")
        )
        email = (request.GET.get("user_email") or "").strip().lower()
        if email:
            qs = qs.filter(user_email__iexact=email)
        return Response({"orders": [_serialize_order(o) for o in qs]})

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .models import BacklinkOrder, BacklinkProduct, PromptTrack

        run = get_object_or_404(AnalysisRun, slug=slug)

        product_id = request.data.get("product_id")
        target_url = (request.data.get("target_url") or "").strip()
        anchor_text = (request.data.get("anchor_text") or "").strip()
        user_email = (request.data.get("user_email") or "").strip().lower()
        track_id = request.data.get("track_id")
        notes = (request.data.get("notes") or "").strip()

        missing = [
            field
            for field, value in [
                ("product_id", product_id),
                ("target_url", target_url),
                ("anchor_text", anchor_text),
                ("user_email", user_email),
            ]
            if not value
        ]
        if missing:
            return Response(
                {"detail": f"Missing required fields: {', '.join(missing)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            product = BacklinkProduct.objects.select_related("provider").get(id=int(product_id))
        except (BacklinkProduct.DoesNotExist, TypeError, ValueError):
            return Response(
                {"detail": "Unknown product_id."},
                status=status.HTTP_404_NOT_FOUND,
            )

        prompt_track = None
        if track_id is not None:
            try:
                prompt_track = PromptTrack.objects.get(id=int(track_id), analysis_run=run)
            except (PromptTrack.DoesNotExist, TypeError, ValueError):
                prompt_track = None

        from apps.integrations.services.backlink_providers import get_client

        order = BacklinkOrder.objects.create(
            provider=product.provider,
            product=product,
            user_email=user_email,
            analysis_run=run,
            prompt_track=prompt_track,
            target_url=target_url[:2048],
            anchor_text=anchor_text[:300],
            status=BacklinkOrder.Status.PENDING_PAYMENT,
            price_cents=product.retail_price_cents,
            currency=product.currency,
            notes_for_provider=notes,
        )

        try:
            client = get_client(product.provider.slug)
            result = client.place_order(
                sku=product.sku,
                target_url=target_url,
                anchor_text=anchor_text,
                notes=notes,
            )
            order.status = result.status or BacklinkOrder.Status.QUEUED
            order.provider_order_id = result.provider_order_id or ""
            order.ordered_at = timezone.now()
            order.save(update_fields=["status", "provider_order_id", "ordered_at"])
        except Exception as exc:
            logger.exception("Immediate backlink order placement failed: %s", exc)

        return Response(_serialize_order(order), status=status.HTTP_201_CREATED)


class BacklinkOrderDetailView(APIView):
    """
    GET  /runs/s/<slug>/backlinks/orders/<int:order_id>/  — refresh status
    POST /runs/s/<slug>/backlinks/orders/<int:order_id>/  — manual sync poll
    """

    permission_classes = [AllowAny]

    def _get_order(self, slug, order_id):
        from django.shortcuts import get_object_or_404

        from .models import BacklinkOrder

        run = get_object_or_404(AnalysisRun, slug=slug)
        return get_object_or_404(
            BacklinkOrder.objects.select_related("provider", "product"),
            id=order_id,
            analysis_run=run,
        )

    def _poll(self, order):
        from apps.integrations.services.backlink_providers import get_client

        from .models import BacklinkOrder

        if not order.provider_order_id:
            return order
        try:
            client = get_client(order.provider.slug)
            res = client.get_status(provider_order_id=order.provider_order_id)
        except Exception as exc:
            logger.warning("Backlink order poll failed (%s): %s", order.id, exc)
            return order

        # Map provider statuses to our enum.
        new_status = (res.status or "").lower() or order.status
        if new_status not in BacklinkOrder.Status.values:
            return order

        if new_status != order.status:
            order.status = new_status
            if res.proof_url:
                order.proof_url = res.proof_url[:2048]
            if new_status == BacklinkOrder.Status.DELIVERED and not order.delivered_at:
                order.delivered_at = timezone.now()
            if res.error_message:
                order.error_message = res.error_message[:1000]
            order.save(update_fields=["status", "proof_url", "delivered_at", "error_message"])
        return order

    def get(self, request, slug, order_id):
        order = self._get_order(slug, order_id)
        return Response(_serialize_order(order))

    def post(self, request, slug, order_id):
        order = self._get_order(slug, order_id)
        order = self._poll(order)
        return Response(_serialize_order(order))

    def delete(self, request, slug, order_id):
        """Cancel/remove an order.

        Hard-delete for draft / pending_payment / cancelled / rejected / refunded
        — these never reached the provider or have already settled. For queued /
        in_progress we soft-delete by flipping status to cancelled, since the
        provider may have started work. Delivered orders refuse deletion (the
        user has already received the link).
        """
        from .models import BacklinkOrder

        order = self._get_order(slug, order_id)
        terminal_safe = {
            BacklinkOrder.Status.DRAFT,
            BacklinkOrder.Status.PENDING_PAYMENT,
            BacklinkOrder.Status.CANCELLED,
            BacklinkOrder.Status.REJECTED,
            BacklinkOrder.Status.REFUNDED,
        }
        if order.status == BacklinkOrder.Status.DELIVERED:
            return Response(
                {"detail": "Delivered orders can't be deleted — they've already been placed."},
                status=400,
            )
        if order.status in terminal_safe:
            order.delete()
            return Response({"deleted": True, "id": order_id})
        # queued / in_progress — soft-cancel.
        order.status = BacklinkOrder.Status.CANCELLED
        order.save(update_fields=["status"])
        return Response(_serialize_order(order))


class BacklinkOrderConfirmPaymentView(APIView):
    """
    POST /runs/s/<slug>/backlinks/orders/<int:order_id>/confirm-payment/

    Finalises a pending_payment order. In production this would be called after
    a successful Stripe payment intent; for now it acts as a mock-checkout
    confirmation that releases the order to the provider.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug, order_id):
        from django.shortcuts import get_object_or_404

        from apps.integrations.services.backlink_providers import get_client

        from .models import BacklinkOrder

        run = get_object_or_404(AnalysisRun, slug=slug)
        order = get_object_or_404(
            BacklinkOrder.objects.select_related("provider", "product"),
            id=order_id,
            analysis_run=run,
        )

        if order.status != BacklinkOrder.Status.PENDING_PAYMENT:
            return Response(
                {"detail": f"Order is already {order.status}; cannot confirm payment."},
                status=status.HTTP_409_CONFLICT,
            )

        payment_intent_id = (request.data.get("payment_intent_id") or "").strip()

        try:
            client = get_client(order.provider.slug)
            result = client.place_order(
                sku=order.product.sku,
                target_url=order.target_url,
                anchor_text=order.anchor_text,
                notes=order.notes_for_provider,
            )
        except Exception as exc:
            logger.exception("Backlink order place_order failed: %s", exc)
            return Response(
                {"detail": f"Provider rejected the order: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        order.status = result.status or BacklinkOrder.Status.QUEUED
        order.provider_order_id = result.provider_order_id or ""
        order.ordered_at = timezone.now()
        if payment_intent_id:
            order.payment_intent_id = payment_intent_id[:120]
        order.save(update_fields=["status", "provider_order_id", "ordered_at", "payment_intent_id"])

        return Response(_serialize_order(order))


# ─────────────────────────────────────────────────────────────────────────────
# Wikipedia draft generator
# Helps the user actually post to Wikipedia: assesses notability, generates a
# neutral encyclopedic draft with citations, lists related articles to edit.
# ─────────────────────────────────────────────────────────────────────────────


class PromptWikipediaDraftView(APIView):
    """
    GET  /runs/s/<slug>/prompts/<int:track_id>/wikipedia/draft/
        Returns the saved draft if one exists, else 404.

    POST /runs/s/<slug>/prompts/<int:track_id>/wikipedia/draft/
        Body: { "force": bool? }
        Generates and persists the Wikipedia kit. Returns the cached version
        if one exists and force is false.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug, track_id):
        from django.shortcuts import get_object_or_404

        from .models import PromptTrack, PromptWikipediaDraft

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, id=track_id, analysis_run=run)
        existing = PromptWikipediaDraft.objects.filter(prompt_track=track).first()
        if not existing:
            return Response({"detail": "No saved draft yet."}, status=status.HTTP_404_NOT_FOUND)
        return Response({**existing.payload, "cached": True})

    def post(self, request, slug, track_id):
        import json
        import re

        from django.shortcuts import get_object_or_404

        from .models import PromptTrack, PromptWikipediaDraft
        from .pipeline.llm import ask_llm

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, id=track_id, analysis_run=run)

        force = bool(request.data.get("force"))
        if not force:
            existing = PromptWikipediaDraft.objects.filter(prompt_track=track).first()
            if existing and existing.payload:
                return Response({**existing.payload, "cached": True})

        brand = (run.brand_name or "").strip() or "the brand"
        url = (run.url or "").strip()
        prompt_text = (track.prompt_text or "").strip()

        # Pull a few signals from existing data the LLM can ground the draft on.
        try:
            competitors = list(run.competitors.values_list("name", flat=True)[:5])
        except Exception:
            competitors = []

        prompt = f"""You are an experienced Wikipedia editor helping a brand build a notable, neutral, well-sourced presence on Wikipedia.

BRAND NAME: {brand}
BRAND URL: {url or "(none)"}
USER QUERY (the prompt they want to be cited for): {prompt_text}
KNOWN COMPETITORS: {", ".join(competitors) or "(none)"}

Produce a JSON object that helps the user actually post to Wikipedia. Cover three things:

1) Notability verdict — does this brand currently meet Wikipedia's notability bar (significant, sustained, independent secondary sources)?
2) A draft article — neutral encyclopedic tone, no marketing language, with placeholder citations as [1], [2], etc.
3) Edit targets — 3-5 EXISTING Wikipedia articles where this brand could plausibly be added as a relevant citation, with the exact one-sentence edit to suggest.

CRITICAL CONSTRAINTS:
- Tone must be neutral, encyclopedic, factual. NEVER use words like "leading", "innovative", "best", "trusted", "premier", "cutting-edge".
- Every claim of fact must reference a citation [n]. References list real, plausible source types (TechCrunch article, Forbes profile, peer-reviewed paper, government registry).
- If the brand likely lacks notability, say so honestly in the verdict — don't fabricate.
- Sections should follow Wikipedia conventions: Lead, History, Products / Services, Reception, References.
- Edit targets must be real existing Wikipedia article titles related to the BRAND or the USER QUERY topic.

Return ONLY valid JSON. No markdown fences. Schema:
{{
  "notability": {{
    "verdict": "qualifies" | "borderline" | "needs_more_coverage",
    "score": <0 to 100>,
    "summary": "Two-sentence summary of why.",
    "missing_evidence": ["Specific gap 1", "Specific gap 2"]
  }},
  "draft": {{
    "title": "Article title",
    "lead": "Markdown lead paragraph (2-4 sentences) with [1] style citations.",
    "sections": [
      {{"heading": "History", "body_markdown": "..."}},
      {{"heading": "Products and services", "body_markdown": "..."}},
      {{"heading": "Reception", "body_markdown": "..."}}
    ],
    "infobox": {{
      "type": "Company",
      "founded": "Year if known, else 'TBD'",
      "headquarters": "City, Country if known",
      "industry": "Industry name",
      "website": "{url or "TBD"}"
    }},
    "references_markdown": "1. Citation 1 source.\\n2. Citation 2 source.\\n3. ..."
  }},
  "edit_targets": [
    {{
      "title": "Existing Wikipedia article title",
      "url": "https://en.wikipedia.org/wiki/Article_Title",
      "suggested_edit": "One sentence to add at a specific section, with [citation needed] placeholder"
    }}
  ],
  "submit_instructions_markdown": "Step-by-step markdown instructions for submitting via Articles for Creation, including the exact AfC link and what to do if the article gets declined."
}}"""

        raw = ask_llm(
            prompt,
            max_tokens=4096,
            temperature=0.2,
            purpose="Wikipedia draft generator",
        )
        if not raw:
            return Response(
                {"detail": "LLM did not respond. Try again in a moment."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Strip code fences and parse. Try progressively looser extraction.
        cleaned = raw.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())

        payload = None
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            # LLM may have added prose before/after the JSON object — extract first {...}
            m = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if m:
                try:
                    payload = json.loads(m.group())
                except json.JSONDecodeError:
                    pass

        if payload is None:
            logger.warning(
                "Wikipedia draft JSON parse failed for slug=%s track=%s. Raw: %s",
                slug,
                track_id,
                cleaned[:300],
            )
            return Response(
                {
                    "detail": "The AI returned an unexpected response. Please retry.",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        PromptWikipediaDraft.objects.update_or_create(prompt_track=track, defaults={"payload": payload})

        return Response({**payload, "cached": False})


class PromptSchemaView(APIView):
    """
    GET  /runs/s/<slug>/prompts/<int:track_id>/schema/
        Returns all previously-generated artifacts for this prompt.

    POST /runs/s/<slug>/prompts/<int:track_id>/schema/
        Body: { "schema_type": "faq"|"article"|"person"|"organization"|"answer",
                "force": bool? }
        If an artifact for this (prompt, schema_type) already exists and force is
        false, it is returned without re-calling the LLM. Set force=true to
        regenerate.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    SCHEMA_TYPES = {"faq", "article", "person", "organization", "answer"}

    def get(self, request, slug, track_id):
        from django.shortcuts import get_object_or_404

        from .models import PromptSchemaArtifact, PromptTrack

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, id=track_id, analysis_run=run)
        artifacts = PromptSchemaArtifact.objects.filter(prompt_track=track)
        return Response(
            {
                "artifacts": [
                    {
                        "schema_type": a.schema_type,
                        "output": a.output,
                        "explanation": a.explanation,
                        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
                    }
                    for a in artifacts
                ]
            }
        )

    def post(self, request, slug, track_id):
        import re

        from django.shortcuts import get_object_or_404

        from .models import PromptSchemaArtifact, PromptTrack
        from .pipeline.llm import ask_llm

        run = get_object_or_404(AnalysisRun, slug=slug)
        track = get_object_or_404(PromptTrack, id=track_id, analysis_run=run)

        schema_type = (request.data.get("schema_type") or "").strip().lower()
        if schema_type not in self.SCHEMA_TYPES:
            return Response(
                {"detail": f"schema_type must be one of {sorted(self.SCHEMA_TYPES)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        force = bool(request.data.get("force"))
        if not force:
            cached = PromptSchemaArtifact.objects.filter(prompt_track=track, schema_type=schema_type).first()
            if cached:
                return Response(
                    {
                        "schema_type": cached.schema_type,
                        "output": cached.output,
                        "explanation": cached.explanation,
                        "cached": True,
                    }
                )

        brand = (run.brand_name or "").strip() or "the brand"
        url = (run.url or "").strip() or "https://example.com"
        prompt_text = (track.prompt_text or "").strip()

        instructions = {
            "faq": (
                "Output ONLY a single FAQPage JSON-LD with one Question whose `name` is the user prompt verbatim, "
                "and an Answer in 2-4 sentences in a neutral, brand voice. Do not invent statistics. "
                "Wrap in `mainEntity` correctly per schema.org."
            ),
            "article": (
                "Output ONLY an Article JSON-LD for a hypothetical page on the brand's domain that answers "
                "the user prompt. Include headline, description, datePublished (today), dateModified (today), "
                "author (Person stub), publisher (Organization with the brand name and logo URL placeholder), "
                "mainEntityOfPage with a plausible URL slug derived from the prompt."
            ),
            "person": (
                "Output ONLY a Person JSON-LD for an author writing about the user prompt's topic for this brand. "
                "Include name (placeholder 'Author Name'), jobTitle, worksFor (the brand), description with their "
                "domain expertise tied to the prompt's topic, sameAs array with placeholder LinkedIn / Twitter "
                "URLs, and knowsAbout array of 3-5 topics relevant to the prompt."
            ),
            "organization": (
                "Output ONLY an Organization JSON-LD for the brand. Include name, url, logo URL placeholder, "
                "description tied to the prompt's topic so the brand reads as a primary source for it, "
                "knowsAbout array, sameAs array with placeholder profile URLs."
            ),
            "answer": (
                "Do NOT output JSON-LD. Output a 2-paragraph direct answer to the user prompt, written in the "
                "brand's voice. Each paragraph 2-3 sentences. Neutral, factual, useful — no marketing words. "
                f"REQUIRED: mention '{brand}' by name in BOTH paragraphs (at least twice total), framed as a "
                f"credible source/practitioner — e.g. 'According to {brand}…', \"{brand}'s formulations…\", "
                f"'In {brand}'s experience…', '{brand} recommends…'. {brand} must read as a domain authority on "
                "this topic, not as a sponsor or marketer. Open with a direct factual answer to the prompt; "
                "use the brand mentions to ground specific claims. End with one practical action the reader can take "
                "(no sales CTA)."
            ),
        }[schema_type]

        prompt = f"""You are generating production-ready content for a brand to paste into their website.

BRAND: {brand}
BRAND URL: {url}
USER PROMPT (the AI search query they want to rank for): {prompt_text}

TASK: {instructions}

CRITICAL:
- For schema types: output ONLY valid JSON-LD wrapped in <script type="application/ld+json">…</script>. No prose.
- For 'answer': output ONLY the 2-paragraph answer. No headings, no bullets, no JSON.
- Use the brand name and URL as given. Use placeholder URLs for logo / images / sameAs profiles.
- Stay neutral. Never use the words "leading", "best", "innovative", "premier", "trusted".
"""

        raw = ask_llm(
            prompt,
            max_tokens=2048,
            temperature=0.2,
            purpose=f"Per-prompt schema generator ({schema_type})",
        )
        if not raw:
            return Response(
                {"detail": "LLM did not respond. Try again in a moment."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        cleaned = raw.strip()
        cleaned = re.sub(r"^```(?:json|html|markdown)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        explanation_map = {
            "faq": "Paste this inside the <head> or near the relevant Q&A on the page targeting this prompt. AI engines lift verbatim Q→A pairs at the highest extraction rate.",
            "article": "Paste this inside the <head> of the article page that targets this prompt. Replace placeholder URLs and the author stub with real values.",
            "person": "Paste this on the author's profile page (e.g. /author/<slug>). Fill in real name, photo URL, LinkedIn, and credentials. Person schema is a direct E-E-A-T signal.",
            "organization": "Paste this in the <head> of your homepage. Replace placeholder logo and sameAs URLs. Organization schema makes you the canonical source for queries about your brand.",
            "answer": "Paste this paragraph directly on the page targeting this prompt — ideally near the top, with the prompt text as an H2 above it. AI engines often lift this verbatim.",
        }

        explanation = explanation_map[schema_type]

        PromptSchemaArtifact.objects.update_or_create(
            prompt_track=track,
            schema_type=schema_type,
            defaults={"output": cleaned, "explanation": explanation},
        )

        return Response(
            {
                "schema_type": schema_type,
                "output": cleaned,
                "explanation": explanation,
                "cached": False,
            }
        )


# ── Content Optimisation (Cursor-style edit + save) ──────────────────────


class ContentPagesView(APIView):
    """GET /api/analyzer/runs/s/<slug>/content/pages/

    Returns the list of pages the user can open in the content editor —
    sourced from the latest sitemap audit, with a fallback to the run's
    root URL.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .services import content_optimisation as co

        run = get_object_or_404(AnalysisRun, slug=slug)
        return Response({"pages": co.list_pages_for_run(run)})


class ContentPageFieldsView(APIView):
    """GET /api/analyzer/runs/s/<slug>/content/page/?url=...

    Returns editable fields + a sandbox-friendly preview HTML for one page.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .services import content_optimisation as co

        url = (request.query_params.get("url") or "").strip()
        if not url:
            return Response({"detail": "url query param required"}, status=400)
        run = get_object_or_404(AnalysisRun, slug=slug)
        try:
            fields = co.fetch_page_fields(run, url)
        except co.ContentOptimisationError as exc:
            return Response({"detail": str(exc)}, status=400)
        # Existing AI suggestions on this page (so a refresh restores them)
        suggestions = [_serialize_content_suggestion(s) for s in co.list_active_suggestions(run, url)]
        return Response({**fields, "suggestions": suggestions})


class ContentSuggestionsView(APIView):
    """POST /api/analyzer/runs/s/<slug>/content/suggestions/  body: {url}

    Generates fresh AI suggestions for a page. Persists ContentSuggestion rows
    and returns them. Old PROPOSED suggestions for the same page are dismissed.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .services import content_optimisation as co

        url = (request.data.get("url") or "").strip()
        if not url:
            return Response({"detail": "url is required"}, status=400)
        run = get_object_or_404(AnalysisRun, slug=slug)
        try:
            suggestions = co.generate_suggestions(run, url)
        except co.ContentOptimisationError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(
            {
                "suggestions": [_serialize_content_suggestion(s) for s in suggestions],
            }
        )


class ContentSuggestionDismissView(APIView):
    """POST /api/analyzer/runs/s/<slug>/content/suggestions/<id>/dismiss/"""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def post(self, request, slug, suggestion_id):
        from django.shortcuts import get_object_or_404

        from .services import content_optimisation as co

        run = get_object_or_404(AnalysisRun, slug=slug)
        s = co.dismiss_suggestion(run, suggestion_id)
        if not s:
            return Response({"detail": "suggestion not found"}, status=404)
        return Response({"ok": True, "id": s.id, "status": s.status})


class ContentSaveView(APIView):
    """POST /api/analyzer/runs/s/<slug>/content/save/

    Body: {url, fields: {title?, meta_description?, body_html?, schema_jsonld?},
           used_suggestion_ids?: [int, ...]}

    Pushes each provided field to the connected plugin (WP/Shopify) and
    marks any used suggestions as USED. 503 if no integration is connected.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .services import content_optimisation as co

        run = get_object_or_404(AnalysisRun, slug=slug)
        url = (request.data.get("url") or "").strip()
        fields = request.data.get("fields") or {}
        used_ids = request.data.get("used_suggestion_ids") or []

        if not url:
            return Response({"detail": "url is required"}, status=400)
        if not isinstance(fields, dict) or not any(fields.get(f) is not None for f in co.ALL_FIELDS):
            return Response({"detail": "fields must include at least one editable field"}, status=400)

        # Filter to known fields only
        edits = {f: fields[f] for f in co.ALL_FIELDS if fields.get(f) is not None}

        try:
            result = co.save_page_edits(run, url, edits)
        except co.ContentOptimisationError as exc:
            return Response(
                {"detail": str(exc), "code": "no_integration"},
                status=503,
            )

        if isinstance(used_ids, list):
            for sid in used_ids:
                try:
                    co.mark_suggestion_used(run, int(sid))
                except (TypeError, ValueError):
                    continue

        return Response(result)


def _serialize_content_suggestion(s):
    return {
        "id": s.id,
        "title": s.title,
        "rationale": s.rationale,
        "target_field": s.target_field,
        "current_excerpt": s.current_excerpt,
        "proposed_value": s.proposed_value,
        "status": s.status,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


class RunBacklinkFreeView(APIView):
    """Site-level free backlink opportunities (no prompt required).

    GET  /runs/s/<slug>/backlinks/free/   — return cached list (generates on
                                            first call if cache empty).
    POST /runs/s/<slug>/backlinks/free/   — force-regenerate via LLM.

    Results are cached in `BrandKit.payload['site_backlink_opportunities']`
    so reload doesn't re-LLM. The LLM call itself is ~5-15 s; users hit POST
    when they want fresh suggestions.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .models import BrandKit
        from .pipeline.site_backlink_opportunities import generate_for_run

        run = get_object_or_404(AnalysisRun, slug=slug)
        kit, _ = BrandKit.objects.get_or_create(analysis_run=run, defaults={"payload": {}})
        cached = (kit.payload or {}).get("site_backlink_opportunities")
        if cached:
            return Response({"rows": cached, "has_generated": True})
        rows = generate_for_run(run)
        if rows:
            kit.payload = {**(kit.payload or {}), "site_backlink_opportunities": rows}
            kit.save(update_fields=["payload", "updated_at"])
        return Response({"rows": rows, "has_generated": bool(rows)})

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .models import BrandKit
        from .pipeline.site_backlink_opportunities import generate_for_run

        run = get_object_or_404(AnalysisRun, slug=slug)
        rows = generate_for_run(run)
        if not rows:
            return Response(
                {"detail": "Generation failed. Try again in a moment."},
                status=502,
            )
        kit, _ = BrandKit.objects.get_or_create(analysis_run=run, defaults={"payload": {}})
        kit.payload = {**(kit.payload or {}), "site_backlink_opportunities": rows}
        kit.save(update_fields=["payload", "updated_at"])
        return Response({"rows": rows, "has_generated": True})


class ContentRewriteElementView(APIView):
    """POST /api/analyzer/runs/s/<slug>/content/rewrite-element/

    Body: {tag, text, instruction?} — ask the LLM to rewrite one element.
    Returns: {new_text}.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .services import content_optimisation as co

        run = get_object_or_404(AnalysisRun, slug=slug)
        tag = (request.data.get("tag") or "p").strip()
        text = (request.data.get("text") or "").strip()
        instruction = (request.data.get("instruction") or "").strip()
        if not text:
            return Response({"detail": "text is required"}, status=400)
        # Suppress unused-arg warning — `run` is here for future per-run telemetry.
        _ = run
        new_text = co.rewrite_element_text(tag, text, instruction)
        return Response({"new_text": new_text})


class ContentApplyElementView(APIView):
    """POST /api/analyzer/runs/s/<slug>/content/apply-element/

    Body: {url, original_text, new_text} — replace the first occurrence of
    `original_text` in the page's body_html with `new_text` and push the new
    body via the connected plugin.

    Returns the same shape as ContentSaveView: {saved, failed, plugin_responses}.
    503 if no plugin is connected. 400 if the text can't be located.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from .services import content_optimisation as co

        run = get_object_or_404(AnalysisRun, slug=slug)
        url = (request.data.get("url") or "").strip()
        original_text = (request.data.get("original_text") or "").strip()
        new_text = (request.data.get("new_text") or "").strip()
        if not url or not original_text or not new_text:
            return Response(
                {"detail": "url, original_text, and new_text are required"},
                status=400,
            )

        try:
            result = co.apply_element_edit(run, url, original_text, new_text)
        except co.ContentOptimisationError as exc:
            msg = str(exc)
            status_code = 503 if "integration" in msg.lower() else 400
            return Response({"detail": msg}, status=status_code)
        return Response(result)


# ── Weekly Email ──────────────────────────────────────────────────────────────


class WeeklyTestEmailView(APIView):
    """POST /api/analyzer/runs/s/<slug>/email/weekly-test/
    Sends a weekly analytics email to the given address using live run data."""

    permission_classes = [AllowAny]

    def post(self, request, slug):
        import datetime
        import urllib.parse

        from apps.analyzer.email_utils import send_weekly_email

        to_email = request.data.get("email", "").strip()
        if not to_email:
            return Response({"error": "email is required."}, status=400)

        try:
            run = AnalysisRun.objects.get(slug=slug)
        except AnalysisRun.DoesNotExist:
            return Response({"error": "Run not found."}, status=404)

        def _domain(url: str) -> str:
            try:
                u = url if url.startswith("http") else f"https://{url}"
                netloc = urllib.parse.urlparse(u).netloc
                # .lstrip("www.") strips any combination of those chars
                # (e.g. "wxample.com" -> "xample.com"); use removeprefix
                # to match the literal "www." only.
                return netloc.removeprefix("www.")
            except Exception:
                return url

        competitors = [
            {
                "name": c.name or "",
                "url": c.url or "",
                "domain": _domain(c.url or ""),
                "composite_score": c.composite_score,
                "relevance_score": c.relevance_score,
            }
            for c in run.competitors.order_by("-relevance_score")[:6]
        ]

        prompts = list(run.prompt_tracks.filter(deleted_at__isnull=True).order_by("-score")[:5])
        recommendations = list(
            run.recommendations.filter(priority__in=["critical", "high"]).order_by("priority")[:5]
        )
        brand_vis = getattr(run, "brand_visibility", None)

        from django.db.models import Case, When
        from django.db.models import IntegerField as DjIntegerField

        from .models import SitemapAudit

        def _page_issue(page):
            sc = page.status_code or 0
            if sc >= 500:
                return ("FAIL", "Server Error", f"HTTP {sc} — server error")
            if sc >= 400:
                return ("FAIL", "Page Not Found", f"HTTP {sc} — unreachable")
            ai_blocked = (
                not page.robots_allows_gptbot
                and not page.robots_allows_claudebot
                and not page.robots_allows_perplexitybot
            )
            if ai_blocked:
                return ("FAIL", "AI Crawlers Blocked", "robots.txt blocks all AI crawlers")
            if page.is_noindex:
                return ("FAIL", "Excluded from Search", "noindex directive set")
            if page.ai_score is not None and page.ai_score < 30:
                return ("FAIL", "Critical AI Visibility Gap", f"AI score {page.ai_score}/100")
            if not page.jsonld_count:
                return ("WARN", "No Structured Data", "Missing JSON-LD schema")
            if not page.robots_allows_gptbot or not page.robots_allows_claudebot:
                return ("WARN", "Partial AI Crawl Block", "Some AI crawlers blocked")
            if not page.has_canonical:
                return ("WARN", "Missing Canonical Tag", "No canonical URL")
            if not page.has_og:
                return ("WARN", "Missing Open Graph Tags", "OG tags absent")
            if page.ai_score is not None and page.ai_score < 60:
                return ("WARN", "Low AI Visibility Score", f"AI score {page.ai_score}/100")
            findings = page.findings if isinstance(page.findings, list) else []
            if findings:
                f = findings[0]
                return (
                    (f.get("severity") or page.severity or "WARN").upper(),
                    f.get("title") or f.get("name") or "Issue Detected",
                    f.get("description") or f.get("message") or "Unresolved issue",
                )
            return ("WARN", "Low AI Visibility", f"AI score {page.ai_score or 0}/100")

        sitemap = SitemapAudit.objects.filter(analysis_run=run).order_by("-created_at").first()
        critical_pages = []
        if sitemap:
            severity_order = Case(
                When(severity="fail", then=0),
                When(severity="warn", then=1),
                default=2,
                output_field=DjIntegerField(),
            )
            raw_pages = sitemap.pages.exclude(severity="ok").order_by(severity_order, "ai_score")[:5]
            for page in raw_pages:
                sev, title, desc = _page_issue(page)
                path = page.path or page.url or ""
                critical_pages.append(
                    {
                        "severity": sev,
                        "title": title,
                        "description": desc,
                        "path": path if len(path) <= 65 else path[:62] + "...",
                        "url": page.url or "",
                    }
                )

        context = {
            "brand_name": run.brand_name or "",
            "url": run.url or "",
            "brand_domain": _domain(run.url or ""),
            "slug": run.slug,
            "score": round(run.composite_score or 0),
            "competitors": competitors,
            "prompts": prompts,
            "recommendations": recommendations,
            "brand_visibility": brand_vis,
            "critical_pages": critical_pages,
            "report_date": datetime.date.today().strftime("%B %d, %Y"),
        }

        try:
            sent = send_weekly_email(to_email, context)
            return Response({"ok": True, "sent": sent})
        except Exception:
            logger.exception("WeeklyTestEmailView error for %s slug=%s", to_email, slug)
            return Response({"ok": False, "error": "Email dispatch failed."}, status=500)


def _resolve_shopify_integration_for_run(run):
    """Return the active Shopify Integration tied to the run's organization,
    or (None, error_response) if there isn't one."""
    if not run.organization_id:
        return None, Response({"detail": "Run is not linked to an organization."}, status=400)
    from apps.integrations.models import Integration

    integ = Integration.objects.filter(
        organization_id=run.organization_id,
        provider=Integration.Provider.SHOPIFY,
        is_active=True,
    ).first()
    if not integ:
        return None, Response(
            {"detail": "No connected Shopify integration for this run's store."},
            status=503,
        )
    return integ, None


class ContentRawFilesListView(APIView):
    """GET /api/analyzer/runs/s/<slug>/content/raw-files/

    Returns the current state of every supported crawler/AI file on the
    connected Shopify store. Shape per item:

        {name, label, kind, url, present, content, default_template}

    `kind` is "theme_asset" (robots.txt) or "page" (everything else served
    at /pages/<handle>).
    """

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from apps.integrations.services.shopify_raw_files import (
            RawFileError,
            list_raw_files,
        )

        run = get_object_or_404(AnalysisRun, slug=slug)
        integ, err = _resolve_shopify_integration_for_run(run)
        if err:
            return err
        try:
            items = list_raw_files(integ)
        except RawFileError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response({"items": items})


class ContentRawFileUpsertView(APIView):
    """PUT /api/analyzer/runs/s/<slug>/content/raw-files/<name>/

    Body: {content: str}. Name is one of: robots, llms, humans, ads.

    For robots.txt the content is written to templates/robots.txt.liquid
    in the active theme. For the others it's saved as the body_html of a
    Shopify Page with handle `<name>-txt`, creating the page if missing.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def put(self, request, slug, name):
        from django.shortcuts import get_object_or_404

        from apps.integrations.services.shopify_raw_files import (
            RAW_FILES,
            RawFileError,
            upsert_raw_file,
        )

        if name not in RAW_FILES:
            return Response(
                {"detail": f"Unknown raw file '{name}'. Allowed: {list(RAW_FILES)}"},
                status=400,
            )
        content = request.data.get("content")
        if content is None:
            return Response({"detail": "content is required"}, status=400)

        run = get_object_or_404(AnalysisRun, slug=slug)
        integ, err = _resolve_shopify_integration_for_run(run)
        if err:
            return err
        try:
            saved = upsert_raw_file(integ, name, content)
        except RawFileError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response(saved)
