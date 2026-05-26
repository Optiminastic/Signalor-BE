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
from rest_framework.permissions import IsAuthenticated
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
