"""
Public API v1 — Bearer-token endpoints for third-party integrations.

All views authenticate via ``BearerTokenAuthentication``; ``request.user``
is a ``PublicApiUser`` carrying ``api_key`` and ``organization``.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.subscription_utils import (
    analysis_allowed_for_email,
    plan_limit_error_response_dict,
    prompt_batch_would_exceed,
)
from apps.analyzer.models import AnalysisRun
from apps.analyzer.tasks import start_analysis_task

from .authentication import BearerTokenAuthentication
from .models import PublicApiUsage
from .serializers import (
    AnalysisSummarySerializer,
    CreateAnalysisSerializer,
    PublicRecommendationSerializer,
)
from .throttling import PublicApiReadThrottle, PublicApiWriteThrottle

logger = logging.getLogger("apps")


class PublicApiView(APIView):
    """Base view: Bearer auth, usage logging, org-scoped lookups."""

    authentication_classes = [BearerTokenAuthentication]
    permission_classes = [IsAuthenticated]
    route_name: str = ""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        request._public_api_started = time.monotonic()

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        api_key = getattr(request, "_public_api_key", None)
        if api_key is not None:
            try:
                started = getattr(request, "_public_api_started", time.monotonic())
                PublicApiUsage.objects.create(
                    api_key=api_key,
                    organization=api_key.organization,
                    route=self.route_name or self.__class__.__name__,
                    method=request.method,
                    status_code=response.status_code,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                # Cheap synchronous touch — single row update, indexed PK.
                api_key.touch()
            except Exception:
                # Never let usage logging break the response.
                logger.exception("public_api usage log failed")
        return response

    @property
    def organization(self):
        return self.request.user.organization

    @property
    def owner_email(self) -> str:
        return (self.organization.owner_email or "").lower().strip()

    def get_run_or_404(self, slug: str):
        try:
            return AnalysisRun.objects.select_related("organization").get(
                slug=slug,
                organization=self.organization,
            )
        except AnalysisRun.DoesNotExist:
            return None


class CreateAnalysisView(PublicApiView):
    """POST /api/v1/public/analyses — kick off a new GEO analysis."""

    throttle_classes = [PublicApiWriteThrottle]
    route_name = "analyses.create"

    def post(self, request):
        serializer = CreateAnalysisSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        email = self.owner_email
        if email:
            allowed, sub_err = analysis_allowed_for_email(email)
            if not allowed:
                return Response({"error": sub_err}, status=status.HTTP_403_FORBIDDEN)
            batch_exceeds, batch_msg = prompt_batch_would_exceed(email, 10)
            if batch_exceeds:
                return Response(
                    plan_limit_error_response_dict(batch_msg),
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Dedupe: if the same URL is already in flight for this org, return it.
        in_flight = [
            AnalysisRun.Status.PENDING,
            AnalysisRun.Status.CRAWLING,
            AnalysisRun.Status.ANALYZING,
            AnalysisRun.Status.SCORING,
        ]
        existing = AnalysisRun.objects.filter(
            organization=self.organization,
            url=data["url"],
            status__in=in_flight,
        ).first()
        if existing:
            return Response(
                AnalysisSummarySerializer(existing).data,
                status=status.HTTP_200_OK,
            )

        run = AnalysisRun.objects.create(
            organization=self.organization,
            url=data["url"],
            brand_name=data.get("brand_name", ""),
            country=data.get("country", ""),
            email=email,
            run_type=data["run_type"],
            status=AnalysisRun.Status.PENDING,
        )
        start_analysis_task(run.id)

        return Response(
            AnalysisSummarySerializer(run).data,
            status=status.HTTP_201_CREATED,
        )


class GetAnalysisView(PublicApiView):
    """GET /api/v1/public/analyses/<slug>/ — status + scores."""

    throttle_classes = [PublicApiReadThrottle]
    route_name = "analyses.get"

    def get(self, request, slug):
        run = self.get_run_or_404(slug)
        if run is None:
            return Response(
                {"error": "Analysis not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(AnalysisSummarySerializer(run).data)


class GetAnalysisRecommendationsView(PublicApiView):
    """GET /api/v1/public/analyses/<slug>/recommendations/"""

    throttle_classes = [PublicApiReadThrottle]
    route_name = "analyses.recommendations"

    def get(self, request, slug):
        run = self.get_run_or_404(slug)
        if run is None:
            return Response(
                {"error": "Analysis not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        recs = run.recommendations.all().order_by("priority", "pillar")
        return Response(
            {
                "slug": run.slug,
                "status": run.status,
                "recommendations": PublicRecommendationSerializer(recs, many=True).data,
            }
        )


class UsageView(PublicApiView):
    """GET /api/v1/public/usage — request volume for the calling key + org."""

    throttle_classes = [PublicApiReadThrottle]
    route_name = "usage"

    def get(self, request):
        api_key = request._public_api_key
        since = timezone.now() - timedelta(days=30)

        org_usage = (
            PublicApiUsage.objects.filter(
                organization=self.organization,
                timestamp__gte=since,
            )
            .values("route")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        key_total = PublicApiUsage.objects.filter(
            api_key=api_key,
            timestamp__gte=since,
        ).count()

        return Response(
            {
                "organization": {
                    "id": self.organization.pk,
                    "name": self.organization.name,
                },
                "key": {
                    "name": api_key.name,
                    "prefix": api_key.key_prefix,
                    "last4": api_key.key_last4,
                    "environment": api_key.environment,
                    "created_at": api_key.created_at,
                    "last_used_at": api_key.last_used_at,
                },
                "window": "30d",
                "requests_by_route": list(org_usage),
                "requests_this_key": key_total,
            }
        )


# ── Satellite blog network (consumed by the external blog sites) ──────────────
# These power the satellite Next.js sites, which have no DB and pull published
# blog posts from here. Guarded by a shared site key (X-Signalor-Site-Key)
# rather than per-org bearer tokens.


def _check_site_key(request) -> bool:
    from django.conf import settings

    key = request.headers.get("X-Signalor-Site-Key", "")
    return bool(settings.SIGNALOR_SITE_KEY) and key == settings.SIGNALOR_SITE_KEY


class PublicSitePostsView(APIView):
    """GET /api/v1/public/sites/<site>/posts/ — published posts for a satellite site."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, site):
        from apps.analyzer.models import SatelliteBlogPost

        if not _check_site_key(request):
            return Response({"error": "invalid site key"}, status=status.HTTP_401_UNAUTHORIZED)
        if site not in dict(SatelliteBlogPost.Site.choices):
            return Response({"error": "unknown site"}, status=status.HTTP_404_NOT_FOUND)
        try:
            limit = min(max(int(request.GET.get("limit", 50)), 1), 100)
            offset = max(int(request.GET.get("offset", 0)), 0)
        except (TypeError, ValueError):
            limit, offset = 50, 0
        qs = SatelliteBlogPost.objects.filter(
            site=site, status=SatelliteBlogPost.Status.PUBLISHED
        ).order_by("-published_at")[offset : offset + limit]
        posts = [
            {
                "slug": p.slug,
                "title": p.title,
                "meta_description": p.meta_description,
                "excerpt": p.excerpt,
                "published_at": p.published_at,
            }
            for p in qs
        ]
        return Response({"site": site, "posts": posts})


class PublicSitePostDetailView(APIView):
    """GET /api/v1/public/sites/<site>/posts/<slug>/ — full published post."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, site, slug):
        from apps.analyzer.models import SatelliteBlogPost

        if not _check_site_key(request):
            return Response({"error": "invalid site key"}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            p = SatelliteBlogPost.objects.get(
                site=site, slug=slug, status=SatelliteBlogPost.Status.PUBLISHED
            )
        except SatelliteBlogPost.DoesNotExist:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            {
                "slug": p.slug,
                "title": p.title,
                "meta_description": p.meta_description,
                "content_html": p.content_html,
                "brand_url": p.brand_url,
                "published_at": p.published_at,
                "site": p.site,
            }
        )
