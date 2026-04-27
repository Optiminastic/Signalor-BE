import hashlib
import hmac
import json
import logging
import os
import secrets
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import requests

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseRedirect
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google.analytics.admin import AnalyticsAdminServiceClient
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.subscription_utils import (
    integration_connect_allowed_for_email,
    plan_limit_error_response_dict,
    project_limit_reached,
)
from apps.organizations.models import Organization

from .models import (
    GADataSnapshot,
    Integration,
    ShopifyDataSnapshot,
    WooCommerceDataSnapshot,
    WordPressDataSnapshot,
)
from .serializers import (
    GADataSnapshotSerializer,
    IntegrationSerializer,
    SelectPropertySerializer,
    ShopifyConnectSerializer,
    ShopifyDataSnapshotSerializer,
    WooCommerceConnectSerializer,
    WooCommerceDataSnapshotSerializer,
    WordPressConnectSerializer,
    WordPressDataSnapshotSerializer,
)

logger = logging.getLogger("apps")

# Must match path("shopify/callback/", ...) under api/integrations/.
_SHOPIFY_OAUTH_CALLBACK_PATH = "/api/integrations/shopify/callback/"


def _resolve_shopify_redirect_uri(request) -> str:
    """
    OAuth redirect_uri must exactly match a URL in the Shopify app allowlist.

    Resolution order for non-local requests:
    1. SHOPIFY_REDIRECT_URI_PUBLIC — canonical HTTPS callback when Host/build_absolute_uri
       is wrong behind some proxies or internal hostnames.
    2. SHOPIFY_REDIRECT_URI — if set and not localhost-only while request is public,
       use request.build_absolute_uri (so allowlist gets the real public URL).
    3. If SHOPIFY_REDIRECT_URI is localhost-only but the request is not local,
       use build_absolute_uri (avoids sending localhost redirect from prod).

    Local requests: SHOPIFY_REDIRECT_URI if set, else build_absolute_uri.
    """
    built = request.build_absolute_uri(_SHOPIFY_OAUTH_CALLBACK_PATH)
    explicit = os.getenv("SHOPIFY_REDIRECT_URI", "").strip()
    public = os.getenv("SHOPIFY_REDIRECT_URI_PUBLIC", "").strip()
    host = (request.get_host() or "").lower().split(":")[0]
    is_local_req = host in ("localhost", "127.0.0.1")
    explicit_mentions_local = bool(
        explicit and ("localhost" in explicit or "127.0.0.1" in explicit)
    )

    def _with_slash(u: str) -> str:
        u = u.strip()
        return u if u.endswith("/") else f"{u}/"

    if not is_local_req and public:
        return _with_slash(public)

    if not explicit:
        return built

    if explicit_mentions_local and not is_local_req:
        return built

    return _with_slash(explicit)


GA_SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
]

# ---------- helpers ----------

def _get_org_or_400(email):
    """Return the org for this email, auto-creating a default one if needed."""
    org = Organization.objects.filter(owner_email=email).first()
    if org:
        return org, None
    reached, msg = project_limit_reached(email)
    if reached:
        return None, Response(
            plan_limit_error_response_dict(msg),
            status=status.HTTP_403_FORBIDDEN,
        )
    org = Organization.objects.create(
        name=email.split("@")[0],
        url="",
        owner_email=email,
    )
    return org, None


def _append_query_params(url: str, extra: dict[str, str]) -> str:
    """Add query keys only if not already present (preserve Shopify install signatures)."""
    parts = urlparse(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    existing_keys = {k for k, _ in pairs}
    for key, value in extra.items():
        if key not in existing_keys:
            pairs.append((key, value))
    new_query = urlencode(pairs)
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, new_query, parts.fragment))


def _resolve_org(email: str, org_id: int | None = None):
    """Resolve org by id (preferred) or fall back to email lookup (auto-creates)."""
    email_norm = email.lower().strip()
    if org_id:
        try:
            org = Organization.objects.get(pk=org_id)
            if (org.owner_email or "").lower().strip() != email_norm:
                return None, Response(
                    {"error": "Organization does not belong to this account."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            return org, None
        except Organization.DoesNotExist:
            pass  # fall through to email lookup
    return _get_org_or_400(email)


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


def _deactivate_other_store_integration(org: Organization, keep_provider: str) -> None:
    """
    Only one store platform (WordPress or Shopify) may be active per organization.
    When connecting `keep_provider`, deactivate the other integration row if present.
    """
    if keep_provider not in (
        Integration.Provider.SHOPIFY,
        Integration.Provider.WORDPRESS,
    ):
        return
    other = (
        Integration.Provider.WORDPRESS
        if keep_provider == Integration.Provider.SHOPIFY
        else Integration.Provider.SHOPIFY
    )
    n = Integration.objects.filter(
        organization=org,
        provider=other,
        is_active=True,
    ).update(is_active=False)
    if n:
        logger.info(
            "Deactivated %s for org %s; %s is now the active store.",
            other,
            org.id,
            keep_provider,
        )


def _redirect_with_status(
    ok: bool,
    reason: str = "",
    return_to: str = "/settings/integrations",
    provider: str = "wordpress",
):
    """Build a redirect to the frontend with a status query param."""
    frontend_base = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")
    target = return_to if return_to.startswith("/") and not return_to.startswith("//") else "/settings/integrations"
    sep = "&" if "?" in target else "?"
    status_q = "connected" if ok else "error"
    url = f"{frontend_base}{target}{sep}{urlencode({provider: status_q, 'reason': reason})}"
    return HttpResponseRedirect(url)


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
    if not creds.refresh_token:
        return creds

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
    """GET /api/integrations/status/?email=&org_id="""
    permission_classes = [AllowAny]
    throttle_classes = []  # high-frequency read for dashboard/sidebar state

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        org_id = request.query_params.get("org_id")
        org_id = int(org_id) if org_id and org_id.isdigit() else None

        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _resolve_org(email, org_id)
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
        payload = serializer.data

        analyzed_url = request.query_params.get("analyzed_url", "").strip()
        if analyzed_url:
            try:
                from .services.ga4 import fetch_ga4_page_metrics
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

        allowed, sub_err = integration_connect_allowed_for_email(data["email"])
        if not allowed:
            return Response(
                {"error": sub_err},
                status=status.HTTP_403_FORBIDDEN,
            )

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
        _deactivate_other_store_integration(org, Integration.Provider.SHOPIFY)

        return Response({
            "message": "Shopify connected successfully.",
            "integration": IntegrationSerializer(integration).data,
        })


class ShopifyAuthURLView(APIView):
    """GET /api/integrations/shopify/auth-url/?email=&shop=&org_id=&return_to="""
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        shop = request.query_params.get("shop", "").strip()
        return_to = request.query_params.get("return_to", "").strip() or "/settings/integrations"
        frontend_base = request.query_params.get("frontend_base", "").strip()
        org_id = request.query_params.get("org_id")
        org_id = int(org_id) if org_id and org_id.isdigit() else None
        storefront_password = request.query_params.get("storefront_password", "").strip()

        if not email or not shop:
            return Response(
                {"error": "Both email and shop parameters are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _resolve_org(email, org_id)
        if err:
            return err

        allowed, sub_err = integration_connect_allowed_for_email(email)
        if not allowed:
            return Response(
                {"error": sub_err},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Prefer the frontend origin supplied by the caller so local/prod
        # environments redirect back to the same place the flow started.
        parsed_frontend = urlparse(frontend_base) if frontend_base else None
        if parsed_frontend and parsed_frontend.scheme in ("http", "https") and parsed_frontend.netloc:
            resolved_frontend_base = f"{parsed_frontend.scheme}://{parsed_frontend.netloc}"
        else:
            resolved_frontend_base = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")

        from .services.shopify import (
            build_shopify_admin_install_custom_app_url,
            build_shopify_oauth_url,
            normalize_shop_domain,
        )

        shop_domain = normalize_shop_domain(shop)
        nonce = secrets.token_urlsafe(24)
        payload = {
            "org_id": org.id,
            "email": email,
            "shop_domain": shop_domain,
            "nonce": nonce,
            "return_to": return_to,
            "frontend_base": resolved_frontend_base,
            "storefront_password": storefront_password,
        }
        cache.set(f"shopify_oauth_state:{nonce}", payload, timeout=15 * 60)
        state = _sign_state(payload)

        client_id = os.getenv("SHOPIFY_CLIENT_ID", "").strip()
        redirect_uri = _resolve_shopify_redirect_uri(request)
        scopes = os.getenv("SHOPIFY_SCOPES", "read_products,read_orders,read_customers")

        if not client_id:
            return Response(
                {"error": "Shopify OAuth env is not configured."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Custom apps: Shopify Admin gives a one-off install URL
        # (admin.shopify.com/oauth/install_custom_app?...&signature=...).
        # Signatures expire and are tied to the store — paste a fresh URL from
        # Shopify → Settings → Apps → Develop apps → your app → Install.
        # We append `state` so /api/integrations/shopify/callback/ can still
        # validate (Shopify forwards it on redirect when supported).
        custom_install = os.getenv("SHOPIFY_CUSTOM_APP_INSTALL_URL", "").strip()
        if custom_install:
            auth_url = _append_query_params(custom_install, {"state": state})
            return Response({"auth_url": auth_url})

        scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
        use_install_custom = os.getenv(
            "SHOPIFY_OAUTH_USE_INSTALL_CUSTOM_APP", ""
        ).strip().lower() in ("1", "true", "yes", "on")
        if use_install_custom:
            auth_url = build_shopify_admin_install_custom_app_url(
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
                scopes=scope_list,
            )
            return Response({"auth_url": auth_url})

        auth_url = build_shopify_oauth_url(
            shop_domain=shop_domain,
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            scopes=scope_list,
        )
        logger.info(
            "Shopify OAuth auth-url: shop=%s redirect_uri=%s",
            shop_domain,
            redirect_uri,
        )
        return Response(
            {
                "auth_url": auth_url,
                "oauth_redirect_uri": redirect_uri,
            }
        )


class ShopifyCallbackView(APIView):
    """GET /api/integrations/shopify/callback/"""
    permission_classes = [AllowAny]

    def get(self, request):
        from .services.shopify import (
            exchange_shopify_oauth_code,
            normalize_shop_domain,
            register_app_uninstalled_webhook,
            validate_shopify_connection,
            verify_shopify_oauth_hmac,
        )

        query_string = request.META.get("QUERY_STRING", "")
        shop = request.query_params.get("shop", "").strip()
        code = request.query_params.get("code", "").strip()
        state_str = request.query_params.get("state", "").strip()

        frontend_base = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")
        default_return_to = "/settings/integrations"

        def _shopify_redirect(ok: bool, reason: str = "", return_to: str = default_return_to):
            target = return_to if return_to.startswith("/") and not return_to.startswith("//") else default_return_to
            sep = "&" if "?" in target else "?"
            status_q = "connected" if ok else "error"
            url = f"{frontend_base}{target}{sep}{urlencode({'shopify': status_q, 'reason': reason})}"
            return HttpResponseRedirect(url)

        if not shop or not code or not state_str:
            return _shopify_redirect(False, "missing_params")

        payload = _verify_state(state_str)
        if not payload:
            return _shopify_redirect(False, "invalid_state")

        frontend_base = (payload.get("frontend_base") or frontend_base).rstrip("/")

        return_to = payload.get("return_to", default_return_to)
        nonce = payload.get("nonce", "")
        cached_payload = cache.get(f"shopify_oauth_state:{nonce}") if nonce else None
        if not nonce or not cached_payload:
            return _shopify_redirect(False, "expired_state", return_to=return_to)
        cache.delete(f"shopify_oauth_state:{nonce}")

        client_secret = os.getenv("SHOPIFY_CLIENT_SECRET", "").strip()
        client_id = os.getenv("SHOPIFY_CLIENT_ID", "").strip()
        if not client_id or not client_secret:
            return _shopify_redirect(False, "oauth_not_configured", return_to=return_to)

        if not verify_shopify_oauth_hmac(query_string, client_secret):
            return _shopify_redirect(False, "invalid_hmac", return_to=return_to)

        shop_domain = normalize_shop_domain(shop)
        # Skip shop mismatch check — Shopify may redirect through a different
        # myshopify.com subdomain than the one the user entered (e.g. custom
        # domains or admin-generated handles like ayx0fj-ze vs arkit-4).

        org_id = payload.get("org_id")
        try:
            org = Organization.objects.get(pk=org_id)
        except Organization.DoesNotExist:
            return _shopify_redirect(False, "org_not_found", return_to=return_to)

        oauth_email = (cached_payload.get("email") or "").lower().strip()
        allowed, _ = integration_connect_allowed_for_email(oauth_email)
        if not allowed:
            return _shopify_redirect(
                False,
                "subscription_required",
                return_to=return_to,
            )

        try:
            tokens = exchange_shopify_oauth_code(
                shop_domain=shop_domain,
                client_id=client_id,
                client_secret=client_secret,
                code=code,
            )
            access_token = tokens.get("access_token", "")
            if not access_token:
                return _shopify_redirect(False, "missing_access_token", return_to=return_to)

            shop_info = validate_shopify_connection(shop_domain, access_token)

            integration, _ = Integration.objects.update_or_create(
                organization=org,
                provider=Integration.Provider.SHOPIFY,
                defaults={"is_active": True},
            )
            integration.set_access_token(access_token)
            # Auto-link Shopify app — use shared app secret for HMAC auth
            shopify_app_url = os.getenv("SIGNALOR_SHOPIFY_APP_URL", "").strip()
            integration.metadata = {
                "shop_domain": shop_domain,
                "shop_name": shop_info.get("name", shop_domain),
                "scope": tokens.get("scope", ""),
                "signalor_app_url": shopify_app_url,
                "signalor_hmac_secret": os.getenv("SHOPIFY_CLIENT_SECRET", ""),
                "storefront_password": payload.get("storefront_password", ""),
            }
            integration.save()
            _deactivate_other_store_integration(org, Integration.Provider.SHOPIFY)

            # Sync session to the Shopify Remix app so it can execute fixes
            if shopify_app_url:
                try:
                    import hashlib as _hashlib
                    import hmac as _hmac
                    import json as _json

                    sync_payload = {
                        "shop": shop_domain,
                        "accessToken": access_token,
                        "scope": tokens.get("scope", ""),
                    }
                    sync_body = _json.dumps(sync_payload).encode("utf-8")
                    hmac_secret = os.getenv("SHOPIFY_CLIENT_SECRET", "")
                    sync_sig = _hmac.new(hmac_secret.encode(), sync_body, _hashlib.sha256).hexdigest()

                    requests.post(
                        f"{shopify_app_url}/api/sync-session",
                        headers={
                            "X-Signalor-Signature": sync_sig,
                            "X-Signalor-Shop": shop_domain,
                            "Content-Type": "application/json",
                        },
                        data=sync_body,
                        timeout=10,
                    )
                    logger.info("Session synced to Shopify app for %s", shop_domain)
                except Exception as sync_exc:
                    logger.warning("Session sync to Shopify app failed (non-fatal): %s", sync_exc)

            # Keep org URL in sync for GEO analysis auto-start
            primary_domain = (
                shop_info.get("domain")
                or shop_info.get("myshopify_domain")
                or shop_domain
            )
            store_url = (
                primary_domain
                if str(primary_domain).startswith("http")
                else f"https://{primary_domain}"
            )
            if org.url != store_url:
                org.url = store_url
                org.save(update_fields=["url"])

            # Do not fail OAuth if webhook registration fails (network, wrong URL, dev vs prod).
            webhook_url = os.getenv("SHOPIFY_APP_UNINSTALLED_WEBHOOK_URL", "").strip()
            if webhook_url:
                try:
                    register_app_uninstalled_webhook(
                        shop_domain, access_token, webhook_url
                    )
                except Exception as webhook_exc:
                    logger.warning(
                        "Shopify app/uninstalled webhook skipped (non-fatal): %s",
                        webhook_exc,
                    )

        except ValueError as exc:
            # exchange_shopify_oauth_code / validate_shopify_connection
            err = str(exc).lower()
            if "token exchange" in err or "failed token exchange" in err:
                reason = "token_exchange_failed"
            elif "shopify_shop_frozen" in err:
                reason = "shop_frozen"
            else:
                reason = "shopify_api_error"
            logger.warning("Shopify OAuth validation: %s", exc)
            return _shopify_redirect(False, reason, return_to=return_to)

        except Exception as exc:
            logger.exception("Shopify callback failed")
            return _shopify_redirect(False, "callback_failed", return_to=return_to)

        return _shopify_redirect(True, return_to=return_to)


class ShopifyAppUninstalledWebhookView(APIView):
    """POST /api/integrations/shopify/webhooks/app-uninstalled/"""
    permission_classes = [AllowAny]

    def post(self, request):
        from .services.shopify import normalize_shop_domain, verify_shopify_webhook_hmac

        secret = os.getenv("SHOPIFY_CLIENT_SECRET", "").strip()
        hmac_header = request.headers.get("X-Shopify-Hmac-Sha256", "")
        shop_header = request.headers.get("X-Shopify-Shop-Domain", "")
        if not secret or not hmac_header or not shop_header:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if not verify_shopify_webhook_hmac(request.body, hmac_header, secret):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        shop_domain = normalize_shop_domain(shop_header)
        integration = Integration.objects.filter(
            provider=Integration.Provider.SHOPIFY,
            metadata__shop_domain=shop_domain,
        ).first()

        if integration:
            integration.shopify_snapshots.all().delete()
            integration.delete()

        return Response({"message": "Processed."}, status=status.HTTP_200_OK)


class ShopifyDisconnectView(APIView):
    """DELETE /api/integrations/shopify/disconnect/?email=&org_id="""
    permission_classes = [AllowAny]

    def delete(self, request):
        email = request.query_params.get("email", "").lower().strip()
        org_id = request.query_params.get("org_id")
        org_id = int(org_id) if org_id and org_id.isdigit() else None

        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _resolve_org(email, org_id)
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
    """POST /api/integrations/shopify/sync/?email=&org_id="""
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.query_params.get("email", "").lower().strip()
        org_id = request.query_params.get("org_id")
        org_id = int(org_id) if org_id and org_id.isdigit() else None

        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _resolve_org(email, org_id)
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
    """GET /api/integrations/shopify/data/?email=&org_id="""
    permission_classes = [AllowAny]

    def get(self, request):
        from datetime import timedelta
        from django.utils import timezone

        email = request.query_params.get("email", "").lower().strip()
        org_id = request.query_params.get("org_id")
        org_id = int(org_id) if org_id and org_id.isdigit() else None

        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _resolve_org(email, org_id)
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


class ShopifyLinkAppView(APIView):
    """POST /api/integrations/shopify/link-app/ — Link the Signalor Shopify app to backend.

    Called by the Shopify Remix app after install. Exchanges HMAC secret and stores
    the app URL so the backend can send fix instructions to the app.

    Body: { "shop_domain": "store.myshopify.com", "app_url": "https://...", "hmac_secret": "..." }
    """
    permission_classes = [AllowAny]

    def post(self, request):
        shop_domain = request.data.get("shop_domain", "").strip()
        app_url = request.data.get("app_url", "").strip()
        hmac_secret = request.data.get("hmac_secret", "").strip()

        if not shop_domain or not app_url or not hmac_secret:
            return Response(
                {"error": "shop_domain, app_url, and hmac_secret are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Find the Shopify integration for this shop
        try:
            integration = Integration.objects.get(
                metadata__shop_domain=shop_domain,
                provider=Integration.Provider.SHOPIFY,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": f"No active Shopify integration found for {shop_domain}."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Store the app URL and HMAC secret in metadata
        meta = integration.metadata or {}
        meta["signalor_app_url"] = app_url.rstrip("/")
        meta["signalor_hmac_secret"] = hmac_secret
        integration.metadata = meta
        integration.save(update_fields=["metadata"])

        return Response({
            "status": "linked",
            "shop_domain": shop_domain,
            "message": "Shopify app linked. Fix instructions will now be routed through the app.",
        })


class WordPressConnectView(APIView):
    """POST /api/integrations/wordpress/connect/ — Plugin API key or WordPress.com OAuth."""

    permission_classes = [AllowAny]

    def post(self, request):
        payload = request.data
        email = (payload.get("email", "") or "").lower().strip()
        site_url = (payload.get("site_url", "") or "").strip()
        api_key = (payload.get("api_key", "") or "").strip()

        if not email or not site_url:
            return Response({"error": "email and site_url are required."}, status=status.HTTP_400_BAD_REQUEST)

        org, err = _get_org_or_400(email)
        if err:
            return err

        allowed, sub_err = integration_connect_allowed_for_email(email)
        if not allowed:
            return Response({"error": sub_err}, status=status.HTTP_403_FORBIDDEN)

        # ── Plugin connect (self-hosted WordPress with Signalor plugin) ──
        if api_key:
            # Verify the plugin is reachable
            verify_url = f"{site_url.rstrip('/')}/wp-json/signalor/v1/status"
            try:
                resp = requests.get(
                    verify_url,
                    headers={"X-Signalor-Key": api_key},
                    timeout=10,
                )
                if not resp.ok:
                    return Response(
                        {"error": f"Could not connect to plugin (HTTP {resp.status_code}). Check your site URL and API key."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                plugin_data = resp.json()
            except requests.RequestException as exc:
                return Response(
                    {"error": f"Could not reach your site: {exc}. Make sure the Signalor GEO plugin is installed and active."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Create/update integration
            integration, _ = Integration.objects.update_or_create(
                organization=org,
                provider=Integration.Provider.WORDPRESS,
                defaults={"is_active": True},
            )
            integration.metadata = {
                "site_url": site_url.rstrip("/"),
                "site_name": plugin_data.get("name", ""),
                "signalor_api_key": api_key,
                "connection_type": "plugin",
            }
            integration.save()
            _deactivate_other_store_integration(org, Integration.Provider.WORDPRESS)

            # Sync org URL
            if org.url != site_url:
                org.url = site_url
                org.save(update_fields=["url"])

            return Response({
                "status": "connected",
                "site_name": plugin_data.get("name", ""),
                "message": f"Connected to {plugin_data.get('name', site_url)} via Signalor plugin.",
            })

        # ── WordPress.com OAuth flow ──
        client_id = os.getenv("WPCOM_CLIENT_ID", "").strip()
        client_secret = os.getenv("WPCOM_CLIENT_SECRET", "").strip()
        redirect_uri = os.getenv("WPCOM_REDIRECT_URI", "").strip()
        if not (client_id and client_secret and redirect_uri):
            return Response(
                {"error": "WordPress.com OAuth is not configured on this server."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        nonce = secrets.token_urlsafe(24)
        state_payload = {
            "nonce": nonce,
            "email": email,
            "site_url": site_url,
            "return_to": (payload.get("return_to") or "").strip(),
            "frontend_base": (payload.get("frontend_base") or "").strip(),
        }
        cache.set(f"wp_oauth_state:{nonce}", state_payload, timeout=15 * 60)
        auth_url = "https://public-api.wordpress.com/oauth2/authorize?" + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "blog": site_url,
                "scope": "global",
                "state": _sign_state(state_payload),
            }
        )
        return Response({
            "oauth_url": auth_url,
            "message": "Redirect to WordPress.com to complete OAuth.",
        })

    def get(self, request):
        return self._connect(request)


class WordPressCallbackView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        def _redirect(ok: bool, reason: str = "", return_to: str = "", frontend_base: str = ""):
            base = frontend_base or os.getenv("FRONTEND_BASE_URL", "http://localhost:3000")
            safe_return = return_to or "/dashboard"
            sep = "&" if "?" in safe_return else "?"
            status_qs = f"wordpress={'connected' if ok else 'error'}"
            if not ok and reason:
                status_qs += f"&reason={reason}"
            return HttpResponseRedirect(f"{base}{safe_return}{sep}{status_qs}")

        code = request.query_params.get("code", "").strip()
        state = request.query_params.get("state", "").strip()
        if not code or not state:
            return _redirect(False, "missing_code_or_state")

        payload = _verify_state(state)
        if not payload:
            return _redirect(False, "invalid_state")

        nonce = payload.get("nonce", "")
        cached = cache.get(f"wp_oauth_state:{nonce}") if nonce else None
        if nonce:
            cache.delete(f"wp_oauth_state:{nonce}")
        if not cached:
            return _redirect(False, "state_expired")

        email = cached.get("email", "").lower().strip()
        site_url = cached.get("site_url", "").strip()
        return_to = cached.get("return_to", "")
        frontend_base = cached.get("frontend_base", "")

        allowed, _ = integration_connect_allowed_for_email(email)
        if not allowed:
            return _redirect(
                False,
                "subscription_required",
                return_to,
                frontend_base,
            )

        client_id = os.getenv("WPCOM_CLIENT_ID", "").strip()
        client_secret = os.getenv("WPCOM_CLIENT_SECRET", "").strip()
        redirect_uri = os.getenv("WPCOM_REDIRECT_URI", "").strip()
        if not (client_id and client_secret and redirect_uri):
            return _redirect(False, "oauth_not_configured", return_to, frontend_base)

        try:
            token_resp = requests.post(
                "https://public-api.wordpress.com/oauth2/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=20,
            )
            if token_resp.status_code != 200:
                return _redirect(False, "token_exchange_failed", return_to, frontend_base)
            token_data = token_resp.json()
            access_token = token_data.get("access_token", "")
            if not access_token:
                return _redirect(False, "missing_access_token", return_to, frontend_base)

            blog_id = str(token_data.get("blog_id") or "").strip()
            blog_url = (token_data.get("blog_url") or "").strip() or site_url

            if not blog_id:
                sites_resp = requests.get(
                    "https://public-api.wordpress.com/rest/v1.1/me/sites",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"fields": "ID,URL,name"},
                    timeout=20,
                )
                if sites_resp.status_code == 200:
                    sites = sites_resp.json().get("sites", [])
                    want = (blog_url or site_url).rstrip("/").lower()
                    for s in sites:
                        su = (s.get("URL") or "").rstrip("/").lower()
                        if su and (su == want or want.endswith(su) or su.endswith(want)):
                            blog_id = str(s.get("ID", ""))
                            break
                    if not blog_id and len(sites) == 1:
                        blog_id = str(sites[0].get("ID", ""))
                        if not blog_url:
                            blog_url = (sites[0].get("URL") or "").strip() or site_url

            me_resp = requests.get(
                "https://public-api.wordpress.com/rest/v1.1/me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=20,
            )
            me = me_resp.json() if me_resp.status_code == 200 else {}
            username = me.get("username", "")
            display_name = me.get("display_name", "") or username

            org, err = _get_org_or_400(email)
            if err:
                return _redirect(False, "org_not_found", return_to, frontend_base)

            integration, _ = Integration.objects.update_or_create(
                organization=org,
                provider=Integration.Provider.WORDPRESS,
                defaults={"is_active": True},
            )
            integration.set_access_token(access_token)
            integration.metadata = {
                "site_url": blog_url or site_url,
                "site_name": display_name or blog_url or site_url,
                "username": username,
                "auth_type": "wpcom_oauth",
                "is_wpcom": True,
                "blog_id": blog_id,
            }
            integration.save()
            _deactivate_other_store_integration(org, Integration.Provider.WORDPRESS)

            canonical_site = (blog_url or site_url).strip()
            if canonical_site and org.url != canonical_site:
                org.url = canonical_site
                org.save(update_fields=["url"])
        except Exception:
            logger.exception("WordPress OAuth callback failed")
            return _redirect(False, "callback_exception", return_to, frontend_base)

        return _redirect(True, return_to=return_to, frontend_base=frontend_base)


class WordPressDisconnectView(APIView):
    """DELETE /api/integrations/wordpress/disconnect/?email="""
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
                provider=Integration.Provider.WORDPRESS,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "WordPress integration not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        integration.wordpress_snapshots.all().delete()
        integration.delete()

        return Response({"message": "WordPress disconnected."})


class WordPressSyncView(APIView):
    """POST /api/integrations/wordpress/sync/?email="""
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
                provider=Integration.Provider.WORDPRESS,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "WordPress not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from .tasks import start_wordpress_sync

        start_wordpress_sync(integration.id)
        return Response({"message": "Sync started."}, status=status.HTTP_202_ACCEPTED)


class WordPressDataView(APIView):
    """GET /api/integrations/wordpress/data/?email="""
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
                provider=Integration.Provider.WORDPRESS,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "WordPress not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        cutoff = timezone.now() - timedelta(days=90)
        integration.wordpress_snapshots.filter(created_at__lt=cutoff).delete()

        snapshot = integration.wordpress_snapshots.first()
        if not snapshot:
            return Response(
                {"error": "No data available. Trigger a sync first."},
                status=status.HTTP_404_NOT_FOUND,
            )

        stale_threshold = timezone.now() - timedelta(hours=24)
        if (
            snapshot.created_at < stale_threshold
            and snapshot.sync_status == "complete"
            and not integration.wordpress_snapshots.filter(sync_status="syncing").exists()
        ):
            from .tasks import start_wordpress_sync

            start_wordpress_sync(integration.id)

        serializer = WordPressDataSnapshotSerializer(snapshot)
        return Response(serializer.data)


# ─────────────────────────────────────────────────────────────
# WooCommerce
# ─────────────────────────────────────────────────────────────

class WooCommerceConnectView(APIView):
    """POST /api/integrations/woocommerce/connect/"""
    permission_classes = [AllowAny]

    def _connect(self, payload):
        serializer = WooCommerceConnectSerializer(data=payload)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        email = data["email"]
        org_id = data.get("org_id")

        org, err = _resolve_org(email, org_id)
        if err:
            return err

        from .services.woocommerce import validate_woocommerce_connection

        try:
            site_info = validate_woocommerce_connection(
                data["site_url"], data["consumer_key"], data["consumer_secret"]
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        integration, _ = Integration.objects.update_or_create(
            organization=org,
            provider=Integration.Provider.WOOCOMMERCE,
            defaults={"is_active": True},
        )
        # consumer_secret → access_token (encrypted)
        integration.set_access_token(data["consumer_secret"])
        integration.metadata = {
            "site_url": site_info["site_url"],
            "site_name": site_info.get("site_name", data["site_url"]),
            "wc_version": site_info.get("wc_version", ""),
            "consumer_key": data["consumer_key"],  # not secret — stored in metadata
        }
        integration.save()

        return Response({
            "message": "WooCommerce connected successfully.",
            "integration": IntegrationSerializer(integration).data,
        })

    def post(self, request):
        return self._connect(request.data)

    def get(self, request):
        # Fallback for accidental GET form submissions from client/UI.
        return self._connect(request.query_params)


class WooCommerceDisconnectView(APIView):
    """DELETE /api/integrations/woocommerce/disconnect/?email=&org_id="""
    permission_classes = [AllowAny]

    def delete(self, request):
        email = request.query_params.get("email", "").lower().strip()
        org_id = request.query_params.get("org_id")
        org_id = int(org_id) if org_id and org_id.isdigit() else None

        if not email:
            return Response({"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        org, err = _resolve_org(email, org_id)
        if err:
            return err

        deleted, _ = Integration.objects.filter(
            organization=org,
            provider=Integration.Provider.WOOCOMMERCE,
        ).delete()

        if not deleted:
            return Response({"error": "WooCommerce not connected."}, status=status.HTTP_404_NOT_FOUND)

        return Response({"message": "WooCommerce disconnected."})


class WooCommerceSyncView(APIView):
    """POST /api/integrations/woocommerce/sync/?email=&org_id="""
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.query_params.get("email") or request.data.get("email", "")).lower().strip()
        org_id = request.query_params.get("org_id") or request.data.get("org_id")
        org_id = int(org_id) if org_id and str(org_id).isdigit() else None

        if not email:
            return Response({"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        org, err = _resolve_org(email, org_id)
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.WOOCOMMERCE,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response({"error": "WooCommerce not connected."}, status=status.HTTP_404_NOT_FOUND)

        from .tasks import start_woocommerce_sync
        start_woocommerce_sync(integration.id)

        return Response({"message": "WooCommerce sync started."})


class WooCommerceDataView(APIView):
    """GET /api/integrations/woocommerce/data/?email=&org_id="""
    permission_classes = [AllowAny]

    def get(self, request):
        from datetime import timedelta
        from django.utils import timezone

        email = request.query_params.get("email", "").lower().strip()
        org_id = request.query_params.get("org_id")
        org_id = int(org_id) if org_id and org_id.isdigit() else None

        if not email:
            return Response({"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        org, err = _resolve_org(email, org_id)
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.WOOCOMMERCE,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response({"error": "WooCommerce not connected."}, status=status.HTTP_404_NOT_FOUND)

        # Prune old snapshots
        cutoff = timezone.now() - timedelta(days=90)
        integration.woocommerce_snapshots.filter(created_at__lt=cutoff).delete()

        snapshot = integration.woocommerce_snapshots.first()
        if not snapshot:
            return Response(
                {"error": "No data available. Trigger a sync first."},
                status=status.HTTP_404_NOT_FOUND,
            )

        stale_threshold = timezone.now() - timedelta(hours=24)
        if (
            snapshot.created_at < stale_threshold
            and snapshot.sync_status == "complete"
            and not integration.woocommerce_snapshots.filter(sync_status="syncing").exists()
        ):
            from .tasks import start_woocommerce_sync
            start_woocommerce_sync(integration.id)

        serializer = WooCommerceDataSnapshotSerializer(snapshot)
        return Response(serializer.data)
