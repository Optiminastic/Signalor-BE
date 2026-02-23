import hashlib
import hmac
import json
import logging
import os
from urllib.parse import urlencode

from django.conf import settings
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google.analytics.admin import AnalyticsAdminServiceClient
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.models import Organization

from .models import GADataSnapshot, Integration, ShopifyDataSnapshot
from .serializers import (
    GADataSnapshotSerializer,
    IntegrationSerializer,
    SelectPropertySerializer,
    ShopifyConnectSerializer,
    ShopifyDataSnapshotSerializer,
)

logger = logging.getLogger("apps")

GA_SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
]

# ---------- helpers ----------

def _get_org_or_400(email):
    """Look up organization by email, return (org, None) or (None, Response)."""
    org = Organization.objects.filter(owner_email=email).first()
    if not org:
        return None, Response(
            {"error": "Organization not found for this email."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return org, None


def _sign_state(payload: dict) -> str:
    """HMAC-sign a JSON state payload."""
    raw = json.dumps(payload, sort_keys=True)
    sig = hmac.new(
        settings.SECRET_KEY.encode(), raw.encode(), hashlib.sha256
    ).hexdigest()
    return json.dumps({"data": payload, "sig": sig})


def _verify_state(state_str: str) -> dict | None:
    """Verify HMAC signature and return payload, or None if invalid."""
    try:
        state = json.loads(state_str)
        raw = json.dumps(state["data"], sort_keys=True)
        expected = hmac.new(
            settings.SECRET_KEY.encode(), raw.encode(), hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(expected, state["sig"]):
            return state["data"]
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    return None


def _build_credentials(integration: Integration) -> Credentials:
    """Build google.oauth2.credentials.Credentials from an Integration."""
    return Credentials(
        token=integration.get_access_token(),
        refresh_token=integration.get_refresh_token(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=GA_SCOPES,
    )


def _refresh_if_needed(integration: Integration, creds: Credentials) -> Credentials:
    """Refresh credentials if expired, persist new tokens.

    Google access tokens live ~1 hour.  The Credentials object built from
    stored tokens has no ``expiry``, so ``creds.expired`` is always False.
    We therefore *always* attempt a refresh when a refresh_token is present
    and catch the "already valid" case silently.
    """
    if not creds.refresh_token:
        return creds

    # If expiry is unknown (None) or the token is expired, force refresh
    needs_refresh = creds.expiry is None or creds.expired
    if needs_refresh:
        try:
            creds.refresh(GoogleRequest())
            integration.set_access_token(creds.token)
            if creds.refresh_token:
                integration.set_refresh_token(creds.refresh_token)
            integration.save(update_fields=[
                "access_token_encrypted", "refresh_token_encrypted", "updated_at",
            ])
        except Exception as exc:
            logger.warning("Token refresh failed: %s", exc)
            raise
    return creds


# ---------- OAuth endpoints ----------

class GAAuthURLView(APIView):
    """GET /api/integrations/google-analytics/auth-url/?email="""
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _get_org_or_400(email)
        if err:
            return err

        state = _sign_state({"org_id": org.id, "email": email})

        params = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_ANALYTICS_REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(GA_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

        return Response({"auth_url": auth_url})


class GACallbackView(APIView):
    """POST /api/integrations/google-analytics/callback/"""
    permission_classes = [AllowAny]

    def post(self, request):
        code = request.data.get("code")
        state_str = request.data.get("state")

        if not code or not state_str:
            return Response(
                {"error": "Both 'code' and 'state' are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = _verify_state(state_str)
        if not payload:
            return Response(
                {"error": "Invalid or tampered state parameter."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org_id = payload.get("org_id")
        try:
            org = Organization.objects.get(pk=org_id)
        except Organization.DoesNotExist:
            return Response(
                {"error": "Organization not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Exchange code for tokens
        import requests as http_requests

        token_resp = http_requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_ANALYTICS_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )

        if token_resp.status_code != 200:
            logger.error("GA4 token exchange failed: %s", token_resp.text)
            return Response(
                {"error": "Failed to exchange authorization code."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tokens = token_resp.json()

        integration, created = Integration.objects.update_or_create(
            organization=org,
            provider=Integration.Provider.GOOGLE_ANALYTICS,
            defaults={"is_active": True},
        )
        integration.set_access_token(tokens["access_token"])
        if tokens.get("refresh_token"):
            integration.set_refresh_token(tokens["refresh_token"])
        integration.save()

        return Response(
            {
                "message": "Google Analytics connected successfully.",
                "integration": IntegrationSerializer(integration).data,
            },
            status=status.HTTP_200_OK,
        )


class IntegrationStatusView(APIView):
    """GET /api/integrations/status/?email="""
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _get_org_or_400(email)
        if err:
            return err

        integrations = Integration.objects.filter(organization=org)
        serializer = IntegrationSerializer(integrations, many=True)
        return Response(serializer.data)


class GADisconnectView(APIView):
    """DELETE /api/integrations/google-analytics/disconnect/?email="""
    permission_classes = [AllowAny]

    def delete(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _get_org_or_400(email)
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.GOOGLE_ANALYTICS,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Google Analytics integration not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Try to revoke the token at Google
        try:
            import requests as http_requests

            http_requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": integration.get_access_token()},
            )
        except Exception:
            logger.warning("Failed to revoke Google token, deleting anyway")

        # Delete snapshots and integration
        integration.ga_snapshots.all().delete()
        integration.delete()

        return Response({"message": "Google Analytics disconnected."})


# ---------- Property selection ----------

class GAPropertiesListView(APIView):
    """GET /api/integrations/google-analytics/properties/?email="""
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _get_org_or_400(email)
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.GOOGLE_ANALYTICS,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Google Analytics not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        creds = _build_credentials(integration)
        creds = _refresh_if_needed(integration, creds)

        try:
            client = AnalyticsAdminServiceClient(credentials=creds)
            accounts = list(client.list_account_summaries())

            properties = []
            for account in accounts:
                for prop in account.property_summaries:
                    properties.append({
                        "property_id": prop.property.split("/")[-1],
                        "display_name": prop.display_name,
                        "account_name": account.display_name,
                    })

            return Response({"properties": properties})

        except Exception as e:
            logger.error("Failed to list GA4 properties: %s", str(e))
            return Response(
                {"error": f"Failed to list properties: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GASelectPropertyView(APIView):
    """POST /api/integrations/google-analytics/select-property/"""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SelectPropertySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        org, err = _get_org_or_400(data["email"])
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.GOOGLE_ANALYTICS,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Google Analytics not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        integration.metadata = {
            **integration.metadata,
            "property_id": data["property_id"],
            "property_name": data.get("property_name", ""),
        }
        integration.save(update_fields=["metadata", "updated_at"])

        return Response({
            "message": "Property selected successfully.",
            "integration": IntegrationSerializer(integration).data,
        })


# ---------- Data sync ----------

class GASyncView(APIView):
    """POST /api/integrations/google-analytics/sync/?email="""
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _get_org_or_400(email)
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.GOOGLE_ANALYTICS,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Google Analytics not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not integration.metadata.get("property_id"):
            return Response(
                {"error": "No GA4 property selected."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .tasks import start_ga4_sync
        start_ga4_sync(integration.id)

        return Response({"message": "Sync started."}, status=status.HTTP_202_ACCEPTED)


class GADataView(APIView):
    """GET /api/integrations/google-analytics/data/?email="""
    permission_classes = [AllowAny]

    def get(self, request):
        from datetime import timedelta
        from django.utils import timezone

        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _get_org_or_400(email)
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.GOOGLE_ANALYTICS,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Google Analytics not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Cleanup: delete snapshots older than 90 days
        cutoff = timezone.now() - timedelta(days=90)
        integration.ga_snapshots.filter(created_at__lt=cutoff).delete()

        snapshot = integration.ga_snapshots.first()  # latest by -created_at
        if not snapshot:
            return Response(
                {"error": "No data available. Trigger a sync first."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Auto-sync if snapshot is stale (>24h) and not currently syncing
        stale_threshold = timezone.now() - timedelta(hours=24)
        if (
            snapshot.created_at < stale_threshold
            and snapshot.sync_status == "complete"
            and not integration.ga_snapshots.filter(sync_status="syncing").exists()
        ):
            from .tasks import start_ga4_sync
            start_ga4_sync(integration.id)

        serializer = GADataSnapshotSerializer(snapshot)
        return Response(serializer.data)


# ---------- Score vs Traffic Correlation ----------

class ScoreTrafficCorrelationView(APIView):
    """GET /api/integrations/score-traffic-correlation/?email="""
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _get_org_or_400(email)
        if err:
            return err

        from apps.analyzer.models import AnalysisRun

        # Get completed analysis runs for this email (last 30)
        runs = AnalysisRun.objects.filter(
            email=email,
            status=AnalysisRun.Status.COMPLETE,
            composite_score__isnull=False,
        ).order_by("created_at")[:30]

        # Get latest GA snapshot
        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.GOOGLE_ANALYTICS,
                is_active=True,
            )
            snapshot = integration.ga_snapshots.filter(
                sync_status="complete",
            ).first()
        except Integration.DoesNotExist:
            snapshot = None

        # Build daily trend lookup from GA data
        ga_daily = {}
        if snapshot and snapshot.daily_trend:
            for day in snapshot.daily_trend:
                ga_daily[day["date"]] = day

        # Build correlation data: pair each analysis run with nearest GA day
        data_points = []
        for run in runs:
            run_date = run.created_at.strftime("%Y-%m-%d")
            ga_day = ga_daily.get(run_date, {})
            data_points.append({
                "date": run_date,
                "geo_score": round(run.composite_score, 1),
                "sessions": ga_day.get("sessions", None),
                "organic_sessions": ga_day.get("organic_sessions", None),
                "url": run.url,
            })

        return Response({
            "data_points": data_points,
            "has_ga_data": bool(snapshot),
        })


# ---------- Shopify endpoints ----------

class ShopifyConnectView(APIView):
    """POST /api/integrations/shopify/connect/"""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ShopifyConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        org, err = _get_org_or_400(data["email"])
        if err:
            return err

        # Validate against Shopify API
        from .services.shopify import validate_shopify_connection

        try:
            shop_info = validate_shopify_connection(
                data["shop_domain"], data["access_token"]
            )
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create or update integration
        integration, _ = Integration.objects.update_or_create(
            organization=org,
            provider=Integration.Provider.SHOPIFY,
            defaults={"is_active": True},
        )
        integration.set_access_token(data["access_token"])
        integration.metadata = {
            "shop_domain": data["shop_domain"],
            "shop_name": shop_info.get("name", data["shop_domain"]),
        }
        integration.save()

        return Response({
            "message": "Shopify connected successfully.",
            "integration": IntegrationSerializer(integration).data,
        })


class ShopifyDisconnectView(APIView):
    """DELETE /api/integrations/shopify/disconnect/?email="""
    permission_classes = [AllowAny]

    def delete(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _get_org_or_400(email)
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.SHOPIFY,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Shopify integration not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        integration.shopify_snapshots.all().delete()
        integration.delete()

        return Response({"message": "Shopify disconnected."})


class ShopifySyncView(APIView):
    """POST /api/integrations/shopify/sync/?email="""
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _get_org_or_400(email)
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.SHOPIFY,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Shopify not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from .tasks import start_shopify_sync
        start_shopify_sync(integration.id)

        return Response({"message": "Sync started."}, status=status.HTTP_202_ACCEPTED)


class ShopifyDataView(APIView):
    """GET /api/integrations/shopify/data/?email="""
    permission_classes = [AllowAny]

    def get(self, request):
        from datetime import timedelta
        from django.utils import timezone

        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _get_org_or_400(email)
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.SHOPIFY,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Shopify not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Cleanup old snapshots
        cutoff = timezone.now() - timedelta(days=90)
        integration.shopify_snapshots.filter(created_at__lt=cutoff).delete()

        snapshot = integration.shopify_snapshots.first()
        if not snapshot:
            return Response(
                {"error": "No data available. Trigger a sync first."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Auto-sync if stale
        stale_threshold = timezone.now() - timedelta(hours=24)
        if (
            snapshot.created_at < stale_threshold
            and snapshot.sync_status == "complete"
            and not integration.shopify_snapshots.filter(sync_status="syncing").exists()
        ):
            from .tasks import start_shopify_sync
            start_shopify_sync(integration.id)

        serializer = ShopifyDataSnapshotSerializer(snapshot)
        return Response(serializer.data)
