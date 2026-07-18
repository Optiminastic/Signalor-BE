"""
Dashboard-facing endpoints for managing API keys.

Identity comes from ``accounts.identity.resolve_request_email`` — a verified better-auth
JWT when the FE sends one, else the legacy ``?email=`` until enforcement is switched on
(``REQUIRE_VERIFIED_IDENTITY``). (Previously this module claimed to be "cookie-authed";
it never was — the email was trusted unverified, which let anyone mint a key for any org.)
They live separately from ``views.py`` (Bearer-auth) so the posture is obvious from the
URL path: ``/api/keys/`` = dashboard, ``/api/v1/public/`` = API.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.identity import resolve_request_email
from apps.organizations.models import Organization

from .dashboard_serializers import (
    ApiKeyListSerializer,
    CreateApiKeySerializer,
    CreateWebhookSerializer,
    NextJsDeploymentListSerializer,
    WebhookListSerializer,
)
from .models import ApiKey, NextJsDeployment, Webhook


def _resolve_org(email: str, org_id: int | None):
    """Match the integrations-app pattern: prefer org_id, verify ownership."""
    email_norm = (email or "").lower().strip()
    if not email_norm:
        return None, Response(
            {"error": "Email is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if org_id:
        try:
            org = Organization.objects.get(pk=org_id)
        except Organization.DoesNotExist:
            return None, Response(
                {"error": "Organization not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if (org.owner_email or "").lower().strip() != email_norm:
            return None, Response(
                {"error": "Organization does not belong to this account."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return org, None
    org = Organization.objects.filter(owner_email=email_norm).first()
    if not org:
        return None, Response(
            {"error": "No organization found for this email."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return org, None


def _parse_org_id(raw: str | None) -> int | None:
    return int(raw) if raw and raw.isdigit() else None


class ApiKeyListCreateView(APIView):
    """GET / POST /api/keys/"""

    permission_classes = [AllowAny]

    def get(self, request):
        # Verified identity (JWT) when present; legacy ?email= until enforcement is on.
        email, err = resolve_request_email(request)
        if err:
            return err
        org_id = _parse_org_id(request.query_params.get("org_id"))
        org, err = _resolve_org(email, org_id)
        if err:
            return err
        keys = ApiKey.objects.filter(organization=org).order_by("-created_at")
        return Response(ApiKeyListSerializer(keys, many=True).data)

    def post(self, request):
        serializer = CreateApiKeySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Ownership must be checked against the *verified* caller, not a body field —
        # otherwise the check compares attacker input to attacker input.
        email, err = resolve_request_email(request)
        if err:
            return err
        org, err = _resolve_org(email, data.get("org_id"))
        if err:
            return err

        key, plaintext = ApiKey.generate(
            organization=org,
            name=data["name"],
            environment=data["environment"],
            created_by_email=email,
        )
        payload = ApiKeyListSerializer(key).data
        # Plaintext returned ONLY here — never persisted, never returned again.
        payload["key"] = plaintext
        return Response(payload, status=status.HTTP_201_CREATED)


class ApiKeyRevokeView(APIView):
    """DELETE /api/keys/<pk>/?email=&org_id=

    Soft-revoke: sets revoked_at. Authentication continues to reject the key,
    but usage history remains queryable.
    """

    permission_classes = [AllowAny]

    def delete(self, request, pk: int):
        email = request.query_params.get("email", "")
        org_id = _parse_org_id(request.query_params.get("org_id"))
        org, err = _resolve_org(email, org_id)
        if err:
            return err
        try:
            key = ApiKey.objects.get(pk=pk, organization=org)
        except ApiKey.DoesNotExist:
            return Response(
                {"error": "API key not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if key.is_revoked:
            return Response(ApiKeyListSerializer(key).data)
        key.revoke()
        return Response(ApiKeyListSerializer(key).data)


class WebhookListCreateView(APIView):
    """GET / POST /api/webhooks/"""

    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "")
        org_id = _parse_org_id(request.query_params.get("org_id"))
        org, err = _resolve_org(email, org_id)
        if err:
            return err
        webhooks = Webhook.objects.filter(organization=org).order_by("-created_at")
        return Response(WebhookListSerializer(webhooks, many=True).data)

    def post(self, request):
        serializer = CreateWebhookSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        org, err = _resolve_org(data["email"], data.get("org_id"))
        if err:
            return err

        webhook, secret = Webhook.create_with_secret(
            organization=org,
            url=data["url"],
            events=data["events"],
            created_by_email=data["email"],
        )
        payload = WebhookListSerializer(webhook).data
        payload["secret"] = secret
        return Response(payload, status=status.HTTP_201_CREATED)


class WebhookDeleteView(APIView):
    """DELETE /api/webhooks/<pk>/?email=&org_id="""

    permission_classes = [AllowAny]

    def delete(self, request, pk: int):
        email = request.query_params.get("email", "")
        org_id = _parse_org_id(request.query_params.get("org_id"))
        org, err = _resolve_org(email, org_id)
        if err:
            return err
        try:
            webhook = Webhook.objects.get(pk=pk, organization=org)
        except Webhook.DoesNotExist:
            return Response(
                {"error": "Webhook not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        webhook.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class NextJsDeploymentListView(APIView):
    """GET /api/integrations/nextjs/deployments/?email=&org_id=&limit=

    Recent Next.js deploys for the org, newest first. Used by the
    dashboard Developers page to surface deploy history.
    """

    permission_classes = [AllowAny]

    DEFAULT_LIMIT = 20
    MAX_LIMIT = 100

    def get(self, request):
        email = request.query_params.get("email", "")
        org_id = _parse_org_id(request.query_params.get("org_id"))
        org, err = _resolve_org(email, org_id)
        if err:
            return err

        # Bounded so a runaway client can't pull years of history per request.
        try:
            limit = min(int(request.query_params.get("limit", self.DEFAULT_LIMIT)), self.MAX_LIMIT)
        except (TypeError, ValueError):
            limit = self.DEFAULT_LIMIT

        deployments = (
            NextJsDeployment.objects.filter(organization=org)
            .select_related("analysis_run")
            .order_by("-created_at")[:limit]
        )
        return Response(NextJsDeploymentListSerializer(deployments, many=True).data)
