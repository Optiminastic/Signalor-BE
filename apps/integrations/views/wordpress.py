"""WordPress: connect, sync, data."""

import os
import secrets
from urllib.parse import urlencode

import requests
from django.core.cache import cache
from django.http import HttpResponseRedirect
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.subscription_utils import (
    integration_connect_allowed_for_email,
)
from core.permissions.throttling import ExpensiveThrottle

from ..models import (
    Integration,
)
from ..serializers import (
    WordPressDataSnapshotSerializer,
)
from ._shared import (
    _deactivate_other_store_integration,
    _get_org_or_400,
    _sign_state,
    _verify_state,
    logger,
)


class WordPressConnectView(APIView):
    """POST /api/integrations/wordpress/connect/ — Plugin API key or WordPress.com OAuth."""

    permission_classes = [AllowAny]

    def post(self, request):
        payload = request.data
        email = (payload.get("email", "") or "").lower().strip()
        site_url = (payload.get("site_url", "") or "").strip()
        api_key = (payload.get("api_key", "") or "").strip()
        username = (payload.get("username", "") or "").strip()

        if not email or not site_url:
            return Response({"error": "email and site_url are required."}, status=status.HTTP_400_BAD_REQUEST)

        org, err = _get_org_or_400(email)
        if err:
            return err

        allowed, sub_err = integration_connect_allowed_for_email(email)
        if not allowed:
            return Response({"error": sub_err}, status=status.HTTP_403_FORBIDDEN)

        # ── Self-hosted WordPress ──
        if api_key:
            if username:
                # Standard WP REST API path using Application Password
                from ..services.wordpress import validate_wordpress_connection

                try:
                    site_info = validate_wordpress_connection(site_url, username, api_key)
                except ValueError as exc:
                    return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

                integration, _ = Integration.objects.update_or_create(
                    organization=org,
                    provider=Integration.Provider.WORDPRESS,
                    defaults={"is_active": True},
                )
                # Store raw app_password — _fetch_selfhosted_data reads it via get_access_token()
                integration.set_access_token(api_key)
                integration.metadata = {
                    "site_url": site_info["site_url"],
                    "site_name": site_info["site_name"],
                    "username": username,
                    "connection_type": "app_password",
                }
                integration.save()
                _deactivate_other_store_integration(org, Integration.Provider.WORDPRESS)

                if org.url != site_info["site_url"]:
                    org.url = site_info["site_url"]
                    org.save(update_fields=["url"])

                return Response(
                    {
                        "status": "connected",
                        "site_name": site_info["site_name"],
                        "message": f"Connected to {site_info['site_name']} via Application Password.",
                    }
                )
            else:
                # Legacy: Signalor plugin path (no username provided)
                verify_url = f"{site_url.rstrip('/')}/wp-json/signalor/v1/status"
                try:
                    resp = requests.get(
                        verify_url,
                        headers={"X-Signalor-Key": api_key},
                        timeout=10,
                    )
                    if not resp.ok:
                        return Response(
                            {
                                "error": f"Could not connect to plugin (HTTP {resp.status_code}). Check your site URL and API key."
                            },
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    plugin_data = resp.json()
                except requests.RequestException as exc:
                    return Response(
                        {
                            "error": f"Could not reach your site: {exc}. Make sure the Signalor GEO plugin is installed and active."
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

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

                if org.url != site_url:
                    org.url = site_url
                    org.save(update_fields=["url"])

                return Response(
                    {
                        "status": "connected",
                        "site_name": plugin_data.get("name", ""),
                        "message": f"Connected to {plugin_data.get('name', site_url)} via Signalor plugin.",
                    }
                )

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
        return Response(
            {
                "oauth_url": auth_url,
                "message": "Redirect to WordPress.com to complete OAuth.",
            }
        )

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
                provider=Integration.Provider.WORDPRESS,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "WordPress not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from ..tasks import start_wordpress_sync

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

        # Clear any dead "syncing" row (killed worker) so it can't wedge auto-sync.
        from ..sync_health import reap_stale_syncing

        reap_stale_syncing(integration.wordpress_snapshots)

        stale_threshold = timezone.now() - timedelta(hours=24)
        if (
            snapshot.created_at < stale_threshold
            and snapshot.sync_status == "complete"
            and not integration.wordpress_snapshots.filter(sync_status="syncing").exists()
        ):
            from ..tasks import start_wordpress_sync

            start_wordpress_sync(integration.id)

        serializer = WordPressDataSnapshotSerializer(snapshot)
        return Response(serializer.data)

