"""Blog automation, the composer and the satellite blog network."""

from datetime import timedelta
from urllib.parse import urlparse

from django.db import DatabaseError
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions.throttling import (
    ExpensiveThrottle,
    PollingThrottle,
)

from ..models import (
    AnalysisRun,
    BlogAutomationConfig,
    BlogAutomationJob,
)
from ..serializers import (
    BlogAutomationConfigSerializer,
    BlogAutomationJobSerializer,
)
from ._shared import (
    BLOG_MODEL_PROVIDER,
    _blog_run_email,
    _blog_source_candidates,
    _brand_ref_for_run,
    _clean_blog_posts,
    _enqueue_daily_jobs,
    _extract_blog_json,
    _generate_blog_draft,
    _generate_blog_html,
    _generate_blog_topics,
    _get_or_create_blog_config,
    _meter_blog_spend,
    _normalize_site,
    _process_due_blog_jobs,
    _publish_blog_job,
    _resolve_blog_integration,
    _resolve_crawl_site,
    _safe_first,
    _short_title,
    _slugify,
    _to_html_from_markdownish,
    logger,
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
        # One aggregate for the five status totals — was 5 round trips.
        from django.db.models import Count, Q

        _s = jobs.aggregate(
            scheduled=Count("id", filter=Q(status=BlogAutomationJob.Status.SCHEDULED)),
            draft=Count("id", filter=Q(status=BlogAutomationJob.Status.DRAFT)),
            needs_review=Count("id", filter=Q(status=BlogAutomationJob.Status.NEEDS_REVIEW)),
            published=Count("id", filter=Q(status=BlogAutomationJob.Status.PUBLISHED)),
            failed=Count("id", filter=Q(status=BlogAutomationJob.Status.FAILED)),
        )
        summary = {
            "scheduled": _s["scheduled"],
            "draft": _s["draft"],
            "needs_review": _s["needs_review"],
            "published": _s["published"],
            "failed": _s["failed"],
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
            run=run,
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

class BlogComposerPostsView(APIView):
    """GET runs/s/<slug>/blog/posts/ — recent WordPress posts for the composer."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        _, email = _blog_run_email(slug)
        if not email:
            return Response({"connected": False, "posts": []})

        integration, provider = _resolve_blog_integration(email)
        if not integration or provider != "wordpress":
            return Response({"connected": False, "posts": []})

        from apps.integrations.services.wordpress import fetch_wordpress_data

        meta = integration.metadata or {}
        try:
            data = fetch_wordpress_data(integration)
        except Exception:
            logger.exception("Blog composer posts fetch failed.")
            return Response(
                {
                    "connected": True,
                    "site_name": meta.get("site_name") or meta.get("site_url", ""),
                    "site_url": meta.get("site_url", ""),
                    "posts": [],
                    "error": "Failed to load recent posts from WordPress.",
                }
            )

        return Response(
            {
                "connected": True,
                "site_name": meta.get("site_name") or meta.get("site_url", ""),
                "site_url": meta.get("site_url", ""),
                "total_posts": int(data.get("total_posts") or 0),
                "published_posts_30d": int(data.get("published_posts_30d") or 0),
                "posts": _clean_blog_posts(data.get("top_posts")),
            }
        )

    def delete(self, request, slug, post_id):
        _, email = _blog_run_email(slug)
        if not email:
            return Response({"error": "Run not found."}, status=status.HTTP_404_NOT_FOUND)

        integration, provider = _resolve_blog_integration(email)
        if not integration or provider != "wordpress":
            return Response({"error": "No connected WordPress site."}, status=status.HTTP_400_BAD_REQUEST)

        from apps.integrations.services.wordpress import delete_wordpress_post

        try:
            delete_wordpress_post(integration, int(post_id))
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Blog composer delete failed.")
            return Response(
                {"error": "Unexpected error while deleting post."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

class BlogComposerGenerateView(APIView):
    """POST runs/s/<slug>/blog/generate/ — AI blog draft (HTML) for the composer."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        run, _ = _blog_run_email(slug)
        if not run:
            return Response({"error": "Run not found."}, status=status.HTTP_404_NOT_FOUND)

        topic = str(request.data.get("topic", "")).strip()
        if not topic:
            return Response({"error": "Topic is required."}, status=status.HTTP_400_BAD_REQUEST)

        tone = str(request.data.get("tone", "informative")).strip() or "informative"
        try:
            word_count = int(request.data.get("word_count") or 800)
        except (TypeError, ValueError):
            word_count = 800
        word_count = max(300, min(word_count, 2500))

        draft = _generate_blog_html(run.url or "", topic, tone, word_count)
        return Response(draft)

class BlogComposerTopicsView(APIView):
    """GET runs/s/<slug>/blog/topics/?refresh=1 — AI topic ideas grounded in
    Search Console keywords + GA + the prompts the brand wants to rank for."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug):
        from django.core.cache import cache

        run, _ = _blog_run_email(slug)
        if not run:
            return Response({"error": "Run not found."}, status=status.HTTP_404_NOT_FOUND)

        key = f"blog_topics:{slug}"
        refresh = str(request.query_params.get("refresh", "")).lower() in ("1", "true", "yes")
        if not refresh:
            try:
                cached = cache.get(key)
            except Exception:
                cached = None
            if cached:
                return Response(cached)

        data = _generate_blog_topics(run)
        if data["topics"]:
            try:
                cache.set(key, data, 6 * 60 * 60)
            except Exception:
                logger.warning("blog topics cache.set failed", exc_info=True)
        return Response(data)

class BlogComposerPublishView(APIView):
    """POST runs/s/<slug>/blog/publish/ — publish a draft to connected WordPress."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        _, email = _blog_run_email(slug)
        if not email:
            return Response({"error": "Run not found."}, status=status.HTTP_404_NOT_FOUND)

        integration, provider = _resolve_blog_integration(email)
        if not integration or provider != "wordpress":
            return Response(
                {"error": "No connected WordPress site. Connect WordPress during onboarding to publish."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        title = str(request.data.get("title", "")).strip()
        content_html = str(request.data.get("content_html", "")).strip()
        if not title or not content_html:
            return Response({"error": "Title and content are required."}, status=status.HTTP_400_BAD_REQUEST)

        publish_status = "publish" if request.data.get("status") == "publish" else "draft"

        from apps.integrations.services.wordpress import publish_wordpress_post

        try:
            result = publish_wordpress_post(
                integration,
                title=title,
                content=content_html,
                excerpt=str(request.data.get("meta_description", "")).strip(),
                status=publish_status,
                slug=str(request.data.get("slug", "")).strip(),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Blog composer publish failed.")
            return Response(
                {"error": "Unexpected error while publishing draft."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        post_id = result.get("id") or 0
        site_url = (integration.metadata or {}).get("site_url", "")
        edit_url = (
            f"{site_url.rstrip('/')}/wp-admin/post.php?post={post_id}&action=edit"
            if post_id and site_url
            else ""
        )
        return Response(
            {
                "post_id": int(post_id) if post_id else 0,
                "post_url": result.get("url") or "",
                "status": result.get("status") or publish_status,
                "edit_url": edit_url,
            }
        )

class BlogComposerUploadImageView(APIView):
    """POST runs/s/<slug>/blog/upload-image/ — upload an image to WP media."""

    permission_classes = [AllowAny]

    def post(self, request, slug):
        _, email = _blog_run_email(slug)
        if not email:
            return Response({"error": "Run not found."}, status=status.HTTP_404_NOT_FOUND)

        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"error": "No file uploaded."}, status=status.HTTP_400_BAD_REQUEST)
        if file_obj.size > 8 * 1024 * 1024:
            return Response({"error": "Image is larger than 8 MB."}, status=status.HTTP_400_BAD_REQUEST)
        content_type = file_obj.content_type or "application/octet-stream"
        if not content_type.startswith("image/"):
            return Response({"error": "Only image files are allowed."}, status=status.HTTP_400_BAD_REQUEST)

        integration, provider = _resolve_blog_integration(email)
        if not integration or provider != "wordpress":
            return Response(
                {"error": "Connect WordPress to upload images to your media library."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.integrations.services.wordpress import upload_wordpress_media

        try:
            result = upload_wordpress_media(
                integration,
                filename=file_obj.name,
                data=file_obj.read(),
                content_type=content_type,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Blog composer image upload failed.")
            return Response(
                {"error": "Unexpected error while uploading image."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if not result.get("url"):
            return Response(
                {"error": "Upload succeeded but no URL was returned."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"url": result["url"], "id": result.get("id") or 0})

class BlogSourcesView(APIView):
    """GET /runs/s/<slug>/blog/sources/ — candidate reference sources (3 competitors + Google + Reddit)."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        return Response({"sources": _blog_source_candidates(run)})

class BlogTitleIdeasView(APIView):
    """POST /runs/s/<slug>/blog/title-ideas/ — ~5 blog title ideas from the run's
    tracked prompts + brand analysis."""

    permission_classes = [AllowAny]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from core.llm.client import ask_llm, cost_scope

        run = get_object_or_404(AnalysisRun, slug=slug)
        site_url = (run.organization.url if run.organization else "") or run.url or ""
        brand = (
            getattr(run, "brand_name", "")
            or (run.organization.name if run.organization else "")
            or urlparse(site_url).netloc
        )
        try:
            prompts = list(
                run.prompt_tracks.filter(deleted_at__isnull=True)
                .order_by("-score")
                .values_list("prompt_text", flat=True)[:10]
            )
        except Exception:
            prompts = []

        prompt = f"""You are a content strategist for the brand "{brand}" ({site_url}).
These are the real AI-search queries people ask in this brand's space:
{chr(10).join(f"- {p}" for p in prompts) if prompts else "- (no tracked prompts yet)"}

Generate 5 compelling, specific blog post titles that would help this brand get
cited in AI search for these topics. Click-worthy, SEO/GEO-friendly, no numbering.
Return STRICT JSON only: {{"titles": ["...", "...", "...", "...", "..."]}}"""

        with cost_scope() as spend:
            raw = ask_llm(
                prompt=prompt,
                preferred_provider=BLOG_MODEL_PROVIDER,
                max_tokens=600,
                temperature=0.85,
                purpose="actions.blog_automation.title_ideas",
            )
        _meter_blog_spend(run, spend, "title_ideas")
        parsed = _extract_blog_json(raw) or {}
        titles = []
        if isinstance(parsed.get("titles"), list):
            titles = [str(t).strip() for t in parsed["titles"] if str(t).strip()][:5]
        if not titles:
            titles = [str(p).strip()[:90] for p in prompts[:5] if str(p).strip()]
        if not titles:
            titles = [f"{brand}: A Practical Guide to AI Search Visibility"]
        return Response({"titles": titles})

class BlogGenerateView(APIView):
    """POST /runs/s/<slug>/blog/generate/ — AI-generate a blog draft for a run
    (used by the Our-backlinks tab). Wraps _generate_blog_draft and returns HTML.
    Accepts optional title (forced), length (short|medium|long), sources (list)."""

    permission_classes = [AllowAny]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        data = request.data or {}
        forced_title = (data.get("title") or "").strip()
        topic = (data.get("topic") or forced_title or "").strip()
        site_url = (run.organization.url if run.organization else "") or run.url or ""
        if not topic:
            brand = getattr(run, "brand_name", "") or urlparse(site_url).netloc
            topic = f"{brand} AI search strategy"
        keywords = data.get("keywords") if isinstance(data.get("keywords"), list) else []
        length = (data.get("length") or "medium").strip()
        sources = data.get("sources") if isinstance(data.get("sources"), list) else None
        try:
            recommendations = list(run.recommendations.values_list("title", flat=True)[:8])
        except Exception:
            recommendations = []

        draft = _generate_blog_draft(
            site_url, topic, keywords, recommendations, length=length, sources=sources, run=run
        )
        title = forced_title or _short_title(draft.get("title", ""))
        slug_val = _slugify(forced_title) if forced_title else draft.get("slug", "")
        content_html = _to_html_from_markdownish(draft.get("content_markdown") or "")

        # Guarantee at least one backlink to the brand site in the content.
        if site_url:
            from ..pipeline.citations import host_of

            brand_host = host_of(site_url)
            if brand_host and brand_host not in content_html:
                brand_name = getattr(run, "brand_name", "") or brand_host
                content_html += (
                    f'\n<p>Learn more about {brand_name} at <a href="{site_url}">{site_url}</a>.</p>'
                )

        # Always provide an AI description: meta -> excerpt -> derived from body.
        meta = (draft.get("meta_description") or draft.get("excerpt") or "").strip()
        if not meta:
            import re as _re

            plain = _re.sub(r"[#*`>_\-]", " ", draft.get("content_markdown") or "")
            plain = " ".join(plain.split())
            meta = plain[:157] + ("…" if len(plain) > 157 else "")

        return Response(
            {
                "title": title,
                "slug": slug_val,
                "meta_description": meta[:300],
                "excerpt": draft.get("excerpt", ""),
                "tags": draft.get("tags", []),
                "content_html": content_html,
                "content_markdown": draft.get("content_markdown", ""),
            }
        )

class BlogPublishNetworkView(APIView):
    """POST /runs/s/<slug>/blog/publish-network/ — publish a generated blog to one
    satellite site. Writes a published BlogPost into the shared blog DB (the site
    reads it) and returns the live URL, which is the backlink shown in "Our backlinks"."""

    permission_classes = [AllowAny]

    def post(self, request, slug):
        from django.conf import settings as dj_settings
        from django.shortcuts import get_object_or_404
        from django.utils import timezone

        from ..models import BlogPost
        from . import blog_store

        run = get_object_or_404(AnalysisRun, slug=slug)
        data = request.data or {}
        site = (data.get("site") or "").strip()
        if site not in dict(BlogPost.Site.choices):
            return Response(
                {"error": "site must be one of: research, listicals, market_trends, comparison, step_guide."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        title = (data.get("title") or "").strip()
        if not title:
            return Response({"error": "title is required."}, status=status.HTTP_400_BAD_REQUEST)

        content_html = data.get("content_html") or ""
        content_markdown = data.get("content_markdown") or ""
        if not content_html and content_markdown:
            content_html = _to_html_from_markdownish(content_markdown)

        base_slug = _slugify(data.get("slug") or title)
        slug_val, n = base_slug, 2
        while blog_store.slug_exists(site, slug_val):
            slug_val = f"{base_slug}-{n}"
            n += 1

        brand_url = (run.organization.url if run.organization else "") or run.url or ""
        now = timezone.now().isoformat()
        post = {
            "id": blog_store.new_id(),
            "site": site,
            "slug": slug_val,
            "title": title[:300],
            "description": (data.get("description") or data.get("meta_description") or "")[:2000],
            "content_html": content_html,
            "image_url": (data.get("image_url") or "")[:2048],
            "category": (data.get("category") or "")[:80],
            "brand_url": brand_url,
            "brand_ref": _brand_ref_for_run(run),
            "source": "signalor",
            "status": "published",
            "published_at": now,
            "created_at": now,
        }
        blog_store.put_post(post)
        domain = (dj_settings.SATELLITE_SITES.get(site) or "").rstrip("/")
        return Response(
            {**post, "url": f"{domain}/{slug_val}" if domain else ""},
            status=status.HTTP_201_CREATED,
        )

class BlogPostDetailView(APIView):
    """GET/PATCH/DELETE /runs/s/<slug>/blog/item/<site>/<post_slug>/ — read, edit,
    or delete one published backlink post in S3 (scoped to this brand)."""

    permission_classes = [AllowAny]

    def _owned(self, run, post):
        return bool(post) and post.get("brand_ref") == _brand_ref_for_run(run)

    def _serialize(self, post):
        from django.conf import settings as dj_settings

        domain = (dj_settings.SATELLITE_SITES.get(post.get("site")) or "").rstrip("/")
        out = dict(post)
        out["category"] = post.get("site")
        out["url"] = f"{domain}/{post.get('slug')}" if domain else ""
        return out

    def get(self, request, slug, site, post_slug):
        from django.shortcuts import get_object_or_404

        from . import blog_store

        run = get_object_or_404(AnalysisRun, slug=slug)
        post = blog_store.get_post(site, post_slug)
        if not self._owned(run, post):
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(self._serialize(post))

    def patch(self, request, slug, site, post_slug):
        from django.shortcuts import get_object_or_404

        from . import blog_store

        run = get_object_or_404(AnalysisRun, slug=slug)
        post = blog_store.get_post(site, post_slug)
        if not self._owned(run, post):
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)

        data = request.data or {}
        fields = {}
        if "title" in data:
            fields["title"] = (str(data.get("title") or "").strip()[:300]) or post.get("title")
        if "description" in data:
            fields["description"] = str(data.get("description") or "")[:2000]
        if "content_html" in data:
            fields["content_html"] = str(data.get("content_html") or "")
        if "image_url" in data:
            fields["image_url"] = str(data.get("image_url") or "")[:2048]
        updated = blog_store.update_post(site, post_slug, fields)
        return Response(self._serialize(updated))

    def delete(self, request, slug, site, post_slug):
        from django.shortcuts import get_object_or_404

        from . import blog_store

        run = get_object_or_404(AnalysisRun, slug=slug)
        post = blog_store.get_post(site, post_slug)
        if not self._owned(run, post):
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        blog_store.delete_post(site, post_slug)
        return Response(status=status.HTTP_204_NO_CONTENT)

class BlogAutoPublishAllView(APIView):
    """POST /runs/s/<slug>/blog/auto-publish-all/ — one click: AI-generate a themed
    blog for each of the 5 satellite sites and publish them all. Limited to once
    per calendar day per brand."""

    permission_classes = [AllowAny]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..services.backlink_engine import auto_can_add_today, run_auto_backlinks

        run = get_object_or_404(AnalysisRun, slug=slug)
        if not auto_can_add_today(run):
            return Response(
                {"error": "You can add backlinks once per day. Try again tomorrow."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        result = run_auto_backlinks(run)
        if result.get("skipped"):
            return Response(
                {"error": "You can add backlinks once per day. Try again tomorrow."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        if not result["created"]:
            return Response(
                {"error": "Failed to generate blogs. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(
            {
                "created": result["created"],
                "errors": result["errors"],
                "can_add_today": auto_can_add_today(run),
            },
            status=status.HTTP_201_CREATED,
        )

class PublicBlogListView(APIView):
    """GET /public/blog/<site>/ — public, read-only list of published posts for one
    satellite site. The 5 sites fetch this (no auth, no brand scope); the backend
    reads S3 with creds so the bucket can stay private."""

    permission_classes = [AllowAny]

    def get(self, request, site):
        from . import blog_store

        site = _normalize_site(site)
        try:
            rows = [p for p in blog_store.list_index(site) if (p.get("status") or "published") == "published"]
        except Exception as exc:
            logger.warning("public-blog-list: S3 read failed for %s: %s", site, exc)
            rows = []
        return Response(rows)

class PublicBlogDetailView(APIView):
    """GET /public/blog/<site>/<post_slug>/ — public, read-only full post (incl.
    content_html) for one satellite site."""

    permission_classes = [AllowAny]

    def get(self, request, site, post_slug):
        from . import blog_store

        site = _normalize_site(site)
        try:
            post = blog_store.get_post(site, post_slug)
        except Exception as exc:
            logger.warning("public-blog-detail: S3 read failed for %s/%s: %s", site, post_slug, exc)
            post = None
        if not post or (post.get("status") and post["status"] != "published"):
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(post)
