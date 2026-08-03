"""Brand kit, overview insights and domain-level analytics."""

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions.throttling import (
    DataForSEOThrottle,
    ExpensiveThrottle,
    PollingThrottle,
)

from ..models import (
    AnalysisRun,
)
from ..serializers import (
    EntityResolutionRequestSerializer,
)
from ._shared import (
    _budget_denied,
    _insights_flag,
    _scoped_run,
    _start_insights_generation,
    logger,
)


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

        from ..services.brand_kit import BrandKitError, get_or_generate

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

        from ..services.brand_kit import BrandKitError, get_or_generate

        run = get_object_or_404(AnalysisRun, slug=slug)
        try:
            return Response({"kit": get_or_generate(run, force=True)})
        except BrandKitError as exc:
            return Response(
                {"detail": str(exc), "code": "kit_generation_failed"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

class OverviewInsightsView(APIView):
    """GET/POST /api/analyzer/runs/s/<slug>/overview-insights/

    GET:  cached AI insight report + compact GA/GSC signal summary. Never fires the
          LLM (cheap, pollable).
    POST: kick off a background (force) regeneration; returns 202. Poll GET until the
          report appears / ``generating`` flips false.
    """

    permission_classes = [AllowAny]

    def get_throttles(self):
        if self.request.method == "POST":
            return [ExpensiveThrottle()]
        return [PollingThrottle()]

    def get(self, request, slug):
        from django.core.cache import cache
        from django.shortcuts import get_object_or_404

        from ..models import OverviewInsightReport
        from ..services.overview_signals import build_overview_signals

        run = get_object_or_404(AnalysisRun, slug=slug)
        report = OverviewInsightReport.objects.filter(analysis_run=run).first()
        return Response(
            {
                "report": report.payload if (report and report.payload) else None,
                "generated_at": report.generated_at.isoformat() if report else None,
                "generating": bool(cache.get(_insights_flag(slug))),
                "signals_summary": build_overview_signals(run),
            }
        )

    def post(self, request, slug):
        from django.core.cache import cache
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        if not cache.get(_insights_flag(slug)):
            cache.set(_insights_flag(slug), True, 300)
            _start_insights_generation(run.id, slug)
        return Response({"status": "generating"}, status=status.HTTP_202_ACCEPTED)

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

        from ..services.domain_analytics import DomainAnalyticsError, get_or_generate

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

class BrandDomainAuthorityView(APIView):
    """GET /api/analyzer/runs/s/<slug>/domain-authority/

    Domain authority for the run's brand — a 0-100 Domain Rating plus (when Ahrefs
    is configured) backlinks and linking websites. Prefers Ahrefs, falls back to
    the free Open PageRank DR. Degrades to a null payload (never errors the
    dashboard) when both upstreams are unavailable. Cached per domain for 7 days.
    """

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from apps.integrations.services.openpagerank import (
            OpenPageRankError,
            OpenPageRankNotConfigured,
        )

        from ..services.domain_authority import get_for_domain
        from ..services.domain_rating import InvalidDomain

        run = get_object_or_404(AnalysisRun, slug=slug)
        org = getattr(run, "organization", None)
        domain = (getattr(org, "url", "") or run.url or "").strip()

        try:
            return Response(get_for_domain(domain))
        except (InvalidDomain, OpenPageRankNotConfigured, OpenPageRankError) as exc:
            # Authority is a best-effort widget — never 500 the dashboard for it.
            logger.info("Domain authority unavailable for run %s: %s", slug, exc)
            return Response(
                {
                    "domain": domain,
                    "domain_rating": None,
                    "global_rank": None,
                    "backlinks": None,
                    "linking_websites": None,
                    "source": None,
                    "fetched_at": None,
                }
            )

class EntityResolutionView(APIView):
    """GET/POST /runs/s/<slug>/entity-resolution/ — do engines know who this brand is?

    Asking about the *name* is the only way to observe name resolution: prompt
    tracking asks category questions, which an engine can answer fully without
    ever resolving the brand.

    GET  — the stored report. Cheap, no LLM call, so the card can render on load.
    POST — refresh it. Billable (one live call per engine) and therefore gated
           three ways: run scoping, the account budget fuse, and a per-run
           cooldown. The cooldown is the one that matters here: a run slug
           travels in browser history and is not a credential, so per-caller
           throttling alone still lets a slug holder bill the account by
           rotating IPs. Spend is capped per brand, not per caller.
    """

    permission_classes = [AllowAny]
    # The view's declared policy: this endpoint is billable, and must never be
    # filed under the generous polling budget (see tests/test_endpoint_policy).
    # ``get_throttles`` narrows it per method at runtime.
    throttle_classes = [ExpensiveThrottle]

    def get_throttles(self):
        # Only the live probe is expensive. Reading the stored report is an
        # ordinary DB fetch and must not eat the expensive allowance.
        if self.request.method == "POST":
            return [ExpensiveThrottle()]
        return [PollingThrottle()]

    def get(self, request, slug):
        from ..services.entity_disambiguation import cached_report

        run, err = _scoped_run(request, slug)
        if err is not None:
            return err
        payload, may_probe = cached_report(run)
        return Response({"report": payload, "may_probe": may_probe})

    def post(self, request, slug):
        from ..services.entity_disambiguation import cached_report, get_or_probe

        run, err = _scoped_run(request, slug)
        if err is not None:
            return err
        denied = _budget_denied(run)
        if denied is not None:
            return denied
        body = EntityResolutionRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        # Refuse rather than silently serving cache, so the client can say why.
        payload, may_probe = cached_report(run)
        if payload is not None and not may_probe:
            return Response(
                {"report": payload, "may_probe": False, "detail": "Recently checked."}
            )
        engines = body.validated_data.get("engines") or None
        report = get_or_probe(run, engines=engines, force=True)
        return Response({"report": report, "may_probe": False})
