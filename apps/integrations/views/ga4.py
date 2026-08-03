"""Google Analytics 4: OAuth, property selection, sync, data."""

from urllib.parse import urlencode

from django.conf import settings
from google.analytics.admin import AnalyticsAdminServiceClient
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.models import Organization
from core.permissions.throttling import ExpensiveThrottle

from ..models import (
    Integration,
)
from ..serializers import (
    GADataSnapshotSerializer,
    IntegrationSerializer,
    SelectPropertySerializer,
)
from ._shared import (
    GA_SCOPES,
    _build_credentials,
    _get_org_or_400,
    _org_id_param,
    _refresh_if_needed,
    _requested_days,
    _resolve_org,
    _sign_state,
    _verify_state,
    logger,
)


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

        org, err = _resolve_org(email, _org_id_param(request))
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
            timeout=15,
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

        org, err = _resolve_org(email, _org_id_param(request))
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
                timeout=15,
            )
        except Exception:
            logger.warning("Failed to revoke Google token, deleting anyway")

        # Delete snapshots and integration
        integration.ga_snapshots.all().delete()
        integration.delete()

        return Response({"message": "Google Analytics disconnected."})

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
                    properties.append(
                        {
                            "property_id": prop.property.split("/")[-1],
                            "display_name": prop.display_name,
                            "account_name": account.display_name,
                        }
                    )

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

        return Response(
            {
                "message": "Property selected successfully.",
                "integration": IntegrationSerializer(integration).data,
            }
        )

class GASyncView(APIView):
    """POST /api/integrations/google-analytics/sync/?email="""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

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

        from ..tasks import start_ga4_sync

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

        # Explicit date range → live fetch for that window (the cached snapshot is
        # a fixed 30-day window). Bounded to keep GA4 quota/latency in check.
        days = _requested_days(request)
        if days:
            try:
                from datetime import date

                from ..services.ga4 import fetch_ga4_data

                data = fetch_ga4_data(integration, days=days)
                end = date.today()
                return Response(
                    {
                        **data,
                        "date_start": (end - timedelta(days=days)).isoformat(),
                        "date_end": end.isoformat(),
                        "sync_status": "complete",
                    }
                )
            except Exception as exc:
                logger.warning("GA live range fetch failed (days=%s): %s", days, exc)
                # Fall through to the cached snapshot rather than erroring the tab.

        # Cleanup: delete snapshots older than 90 days
        cutoff = timezone.now() - timedelta(days=90)
        integration.ga_snapshots.filter(created_at__lt=cutoff).delete()

        snapshot = integration.ga_snapshots.first()  # latest by -created_at
        if not snapshot:
            return Response(
                {"error": "No data available. Trigger a sync first."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Clear any dead "syncing" row (killed worker) so it can't wedge auto-sync.
        from ..sync_health import reap_stale_syncing

        reap_stale_syncing(integration.ga_snapshots)

        # Auto-sync if snapshot is stale (>24h) and not currently syncing
        stale_threshold = timezone.now() - timedelta(hours=24)
        if (
            snapshot.created_at < stale_threshold
            and snapshot.sync_status == "complete"
            and not integration.ga_snapshots.filter(sync_status="syncing").exists()
        ):
            from ..tasks import start_ga4_sync

            start_ga4_sync(integration.id)

        serializer = GADataSnapshotSerializer(snapshot)
        payload = serializer.data

        analyzed_url = request.query_params.get("analyzed_url", "").strip()
        if analyzed_url:
            try:
                from ..services.ga4 import fetch_ga4_page_metrics

                payload["page_match"] = fetch_ga4_page_metrics(integration, analyzed_url)
            except Exception as exc:
                logger.warning("Failed GA page match lookup: %s", exc)
                payload["page_match"] = {
                    "found": False,
                    "page_path": "",
                    "sessions": 0,
                    "bounce_rate": 0.0,
                    "avg_session_duration": 0.0,
                }

        return Response(payload)

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
            data_points.append(
                {
                    "date": run_date,
                    "geo_score": round(run.composite_score, 1),
                    "sessions": ga_day.get("sessions", None),
                    "organic_sessions": ga_day.get("organic_sessions", None),
                    "url": run.url,
                }
            )

        return Response(
            {
                "data_points": data_points,
                "has_ga_data": bool(snapshot),
            }
        )

