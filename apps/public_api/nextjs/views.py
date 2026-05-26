"""
Public endpoints consumed by @signalor/nextjs.

All three reuse the Bearer-token auth + usage logging from the existing
``PublicApiView`` base. The SDK only needs an API key — same minting flow
as Webflow/Framer.

  POST /api/v1/public/nextjs/register       — first-call handshake
  POST /api/v1/public/nextjs/deploy         — deploy notification
  POST /api/v1/public/nextjs/metadata/bulk  — push per-page metadata
"""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from apps.accounts.subscription_utils import (
    analysis_allowed_for_email,
    plan_limit_error_response_dict,
    prompt_batch_would_exceed,
)
from apps.analyzer.models import AnalysisRun
from apps.analyzer.tasks import start_analysis_task
from apps.integrations.models import Integration

from ..models import NextJsDeployment
from ..throttling import PublicApiReadThrottle, PublicApiWriteThrottle
from ..views import PublicApiView
from .serializers import (
    BulkMetadataRequestSerializer,
    DeploymentResponseSerializer,
    DeployRequestSerializer,
    RegisterResponseSerializer,
)

logger = logging.getLogger("apps")


def _ensure_integration(organization) -> Integration:
    """Mark the org as having a Next.js integration on first contact.

    Idempotent — re-installs don't duplicate the row. The Integration row
    isn't load-bearing (no token stored), but the dashboard uses it to
    show "Next.js connected" on the integrations panel.
    """
    integration, _ = Integration.objects.update_or_create(
        organization=organization,
        provider=Integration.Provider.NEXTJS,
        defaults={"is_active": True},
    )
    return integration


def _default_llms_txt(org_name: str, org_url: str) -> str:
    """Minimal llms.txt template. The SDK fills in pages later from
    sitemap discovery; this is the baseline served before that runs."""
    return (
        f"# {org_name}\n"
        f"\n"
        f"> {org_url}\n"
        f"\n"
        f"## About\n"
        f"\n"
        f"This site is monitored by Signalor for AI search visibility.\n"
    )


class RegisterView(PublicApiView):
    """POST /api/v1/public/nextjs/register

    Called by the SDK on app startup. Returns the schema defaults + llms.txt
    template the SDK should serve, so a freshly installed package works with
    zero config — the dev's Organization profile drives all of it.
    """

    throttle_classes = [PublicApiReadThrottle]
    route_name = "nextjs.register"

    def post(self, request):
        org = self.organization
        _ensure_integration(org)

        schema_defaults: dict[str, Any] = {
            "Organization": {
                "@type": "Organization",
                "name": org.name,
                "url": org.url or "",
            },
            "WebSite": {
                "@type": "WebSite",
                "name": org.name,
                "url": org.url or "",
            },
        }

        payload = {
            "organization": {
                "id": org.pk,
                "name": org.name,
                "url": org.url or "",
            },
            "schema_defaults": schema_defaults,
            "llms_txt_template": _default_llms_txt(org.name, org.url or ""),
            "sitemap_overrides": {
                # Devs can opt routes out via SDK config; backend has no
                # opinion yet, so the default override list is empty.
                "exclude_paths": [],
            },
        }
        return Response(RegisterResponseSerializer(payload).data)


class DeployView(PublicApiView):
    """POST /api/v1/public/nextjs/deploy

    Fired by the ``signalor-deploy`` postbuild script. Creates a
    NextJsDeployment row and kicks off an AnalysisRun for the deployed URL.
    """

    throttle_classes = [PublicApiWriteThrottle]
    route_name = "nextjs.deploy"

    def post(self, request):
        serializer = DeployRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        org = self.organization
        email = self.owner_email

        # The dev's URL is the source of truth, but fall back to the org's
        # known URL if the SDK couldn't determine it (e.g. local builds).
        deploy_url = (data.get("url") or org.url or "").strip()

        deployment = NextJsDeployment.objects.create(
            organization=org,
            commit_sha=data.get("commit_sha", ""),
            environment=data.get("environment", NextJsDeployment.Environment.PRODUCTION),
            url=deploy_url,
            host=data.get("host", ""),
            build_metadata=data.get("build_metadata") or {},
            deployed_at=timezone.now(),
        )
        _ensure_integration(org)

        # No URL → nothing to analyze. Return the deployment so the SDK can
        # still log it; the dashboard surfaces "no URL" as a status reason.
        if not deploy_url:
            deployment.status = NextJsDeployment.Status.FAILED
            deployment.error_message = "No URL provided and organization has no default URL."
            deployment.save(update_fields=["status", "error_message"])
            return Response(
                DeploymentResponseSerializer(deployment).data,
                status=status.HTTP_201_CREATED,
            )

        # Honor existing plan limits — same gating as Bearer-auth /analyses.
        if email:
            allowed, sub_err = analysis_allowed_for_email(email)
            if not allowed:
                deployment.status = NextJsDeployment.Status.FAILED
                deployment.error_message = sub_err
                deployment.save(update_fields=["status", "error_message"])
                return Response({"error": sub_err}, status=status.HTTP_403_FORBIDDEN)
            batch_exceeds, batch_msg = prompt_batch_would_exceed(email, 10)
            if batch_exceeds:
                deployment.status = NextJsDeployment.Status.FAILED
                deployment.error_message = batch_msg
                deployment.save(update_fields=["status", "error_message"])
                return Response(
                    plan_limit_error_response_dict(batch_msg),
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Dedupe: if the same URL is already in flight for this org, reuse
        # that run instead of spawning a duplicate.
        in_flight = [
            AnalysisRun.Status.PENDING,
            AnalysisRun.Status.CRAWLING,
            AnalysisRun.Status.ANALYZING,
            AnalysisRun.Status.SCORING,
        ]
        run = AnalysisRun.objects.filter(
            organization=org,
            url=deploy_url,
            status__in=in_flight,
        ).first() or AnalysisRun.objects.create(
            organization=org,
            url=deploy_url,
            email=email,
            run_type=AnalysisRun.RunType.SINGLE_PAGE,
            status=AnalysisRun.Status.PENDING,
        )
        # Only kick the background task for runs we just created.
        if run.status == AnalysisRun.Status.PENDING and not run.progress:
            start_analysis_task(run.id)

        deployment.analysis_run = run
        deployment.status = NextJsDeployment.Status.ANALYZING
        deployment.save(update_fields=["analysis_run", "status"])

        return Response(
            DeploymentResponseSerializer(deployment).data,
            status=status.HTTP_201_CREATED,
        )


class BulkMetadataView(PublicApiView):
    """POST /api/v1/public/nextjs/metadata/bulk

    Push per-page metadata for the most recent deployment, so the analyzer
    can skip crawling pages the SDK already has data on.
    """

    throttle_classes = [PublicApiWriteThrottle]
    route_name = "nextjs.metadata.bulk"

    def post(self, request):
        serializer = BulkMetadataRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            deployment = NextJsDeployment.objects.get(
                pk=data["deployment_id"],
                organization=self.organization,
            )
        except NextJsDeployment.DoesNotExist:
            return Response(
                {"error": "Deployment not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Replace rather than merge — the SDK is authoritative for what it
        # just built. Merging would leave stale entries for deleted routes.
        deployment.pages_metadata = data["pages"]
        deployment.save(update_fields=["pages_metadata"])

        return Response(
            {
                "deployment_id": deployment.pk,
                "pages_received": len(data["pages"]),
            }
        )
