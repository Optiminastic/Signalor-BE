"""Google Search Console: OAuth, site selection, sync, coverage, sitemaps."""

from urllib.parse import urlencode, urlparse

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.models import Organization
from core.permissions.throttling import ExpensiveThrottle, PollingThrottle

from ..models import (
    Integration,
)
from ..serializers import (
    GSCDataSnapshotSerializer,
    IntegrationSerializer,
    SelectSiteSerializer,
)
from ._shared import (
    GSC_SCOPES,
    _get_org_or_400,
    _gsc_redirect,
    _org_id_param,
    _requested_days,
    _resolve_org,
    _sign_state,
    _verify_state,
    logger,
)


class GSCAuthURLView(APIView):
    """GET /api/integrations/google-search-console/auth-url/?email=&return_to=&frontend_base="""

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

        # Where to land the user back on the frontend after OAuth completes.
        return_to = request.query_params.get("return_to", "").strip() or "/"
        frontend_base = request.query_params.get("frontend_base", "").strip()
        parsed = urlparse(frontend_base) if frontend_base else None
        if parsed and parsed.scheme in ("http", "https") and parsed.netloc:
            resolved_frontend_base = f"{parsed.scheme}://{parsed.netloc}"
        else:
            resolved_frontend_base = ""

        state = _sign_state(
            {
                "org_id": org.id,
                "email": email,
                "return_to": return_to,
                "frontend_base": resolved_frontend_base,
            }
        )

        params = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_SEARCH_CONSOLE_REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(GSC_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
        return Response({"auth_url": auth_url})

class GSCCallbackView(APIView):
    """GET /api/integrations/google-search-console/callback/ — Google redirects here."""

    permission_classes = [AllowAny]

    def get(self, request):
        code = request.query_params.get("code", "").strip()
        state_str = request.query_params.get("state", "").strip()

        payload = _verify_state(state_str) if state_str else None
        frontend_base = (payload or {}).get("frontend_base", "")
        return_to = (payload or {}).get("return_to", "/")

        if not code or not payload:
            return _gsc_redirect(False, frontend_base, return_to, "invalid_state")

        org_id = payload.get("org_id")
        try:
            org = Organization.objects.get(pk=org_id)
        except Organization.DoesNotExist:
            return _gsc_redirect(False, frontend_base, return_to, "org_not_found")

        import requests as http_requests

        token_resp = http_requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_SEARCH_CONSOLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=20,
        )
        if token_resp.status_code != 200:
            logger.error("GSC token exchange failed: %s", token_resp.text)
            return _gsc_redirect(False, frontend_base, return_to, "token_exchange_failed")

        tokens = token_resp.json()

        integration, _ = Integration.objects.update_or_create(
            organization=org,
            provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE,
            defaults={"is_active": True},
        )
        integration.set_access_token(tokens["access_token"])
        if tokens.get("refresh_token"):
            integration.set_refresh_token(tokens["refresh_token"])
        integration.save()

        return _gsc_redirect(True, frontend_base, return_to)

class GSCDisconnectView(APIView):
    """DELETE /api/integrations/google-search-console/disconnect/?email="""

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
                provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Search Console integration not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            import requests as http_requests

            http_requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": integration.get_access_token()},
                timeout=10,
            )
        except Exception:
            logger.warning("Failed to revoke Google token, deleting anyway")

        integration.gsc_snapshots.all().delete()
        integration.delete()

        return Response({"message": "Search Console disconnected."})

class GSCSitesListView(APIView):
    """GET /api/integrations/google-search-console/sites/?email="""

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
                provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Search Console not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            from ..services.gsc import list_gsc_sites

            return Response({"sites": list_gsc_sites(integration)})
        except Exception as e:
            logger.error("Failed to list GSC sites: %s", str(e))
            return Response(
                {"error": f"Failed to list properties: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

class GSCSelectSiteView(APIView):
    """POST /api/integrations/google-search-console/select-site/"""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SelectSiteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        org, err = _get_org_or_400(data["email"])
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Search Console not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        integration.metadata = {
            **integration.metadata,
            "site_url": data["site_url"],
        }
        integration.save(update_fields=["metadata", "updated_at"])

        return Response(
            {
                "message": "Property selected successfully.",
                "integration": IntegrationSerializer(integration).data,
            }
        )

class GSCSyncView(APIView):
    """POST /api/integrations/google-search-console/sync/?email="""

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
                provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Search Console not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not integration.metadata.get("site_url"):
            return Response(
                {"error": "No Search Console property selected."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from ..tasks import start_gsc_sync

        start_gsc_sync(integration.id)

        return Response({"message": "Sync started."}, status=status.HTTP_202_ACCEPTED)

class GSCDataView(APIView):
    """GET /api/integrations/google-search-console/data/?email=&analyzed_url="""

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
                provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Search Console not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Explicit date range → live fetch for that window (the cached snapshot is
        # a fixed 28-day window). Bounded to keep GSC quota/latency in check.
        days = _requested_days(request)
        if days:
            try:
                from ..services.gsc import fetch_gsc_data

                data = fetch_gsc_data(integration, days=days)
                return Response({**data, "sync_status": "complete"})
            except Exception as exc:
                logger.warning("GSC live range fetch failed (days=%s): %s", days, exc)
                # Fall through to the cached snapshot rather than erroring the tab.

        # Cleanup: delete snapshots older than 90 days
        cutoff = timezone.now() - timedelta(days=90)
        integration.gsc_snapshots.filter(created_at__lt=cutoff).delete()

        snapshot = integration.gsc_snapshots.first()  # latest by -created_at
        if not snapshot:
            return Response(
                {"error": "No data available. Trigger a sync first."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Clear any dead "syncing" row (killed worker) so it can't wedge auto-sync.
        from ..sync_health import reap_stale_syncing

        reap_stale_syncing(integration.gsc_snapshots)

        # Auto-sync if snapshot is stale (>24h) and not currently syncing
        stale_threshold = timezone.now() - timedelta(hours=24)
        if (
            snapshot.created_at < stale_threshold
            and snapshot.sync_status == "complete"
            and not integration.gsc_snapshots.filter(sync_status="syncing").exists()
        ):
            from ..tasks import start_gsc_sync

            start_gsc_sync(integration.id)

        serializer = GSCDataSnapshotSerializer(snapshot)
        payload = serializer.data

        analyzed_url = request.query_params.get("analyzed_url", "").strip()
        if analyzed_url:
            try:
                from ..services.gsc import fetch_gsc_page_metrics

                payload["page_match"] = fetch_gsc_page_metrics(integration, analyzed_url)
            except Exception as exc:
                logger.warning("Failed GSC page match lookup: %s", exc)
                payload["page_match"] = {
                    "found": False,
                    "page": "",
                    "clicks": 0,
                    "impressions": 0,
                    "ctr": 0.0,
                    "position": 0.0,
                }

        return Response(payload)

class GSCUrlInspectView(APIView):
    """GET /api/integrations/google-search-console/inspect/?email=&url="""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        url_to_inspect = request.query_params.get("url", "").strip()
        if not email or not url_to_inspect:
            return Response(
                {"error": "Both 'email' and 'url' parameters are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _get_org_or_400(email)
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Search Console not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            from ..services.gsc import inspect_gsc_url

            return Response(inspect_gsc_url(integration, url_to_inspect))
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.error("GSC URL inspection error: %s", exc)
            return Response(
                {"error": "URL inspection failed."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

class GSCCoverageView(APIView):
    """
    GET /api/integrations/google-search-console/coverage/?email=

    Returns the authoritative Indexed / Not-indexed split from a cached per-URL
    URL-Inspection pass (Search Console's "Page indexing" data has no bulk API).
    Triggers a background refresh when the cache is missing or stale (>24h), and
    enriches indexed pages with live Search metrics.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

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
                provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Search Console not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from ..tasks import start_gsc_index_sync

        # A "syncing" snapshot older than this is treated as dead (e.g. the worker
        # thread was killed by a server restart) and no longer blocks a refresh.
        sync_timeout = timezone.now() - timedelta(minutes=10)
        integration.gsc_index_snapshots.filter(sync_status="syncing", created_at__lt=sync_timeout).update(
            sync_status="failed", error_message="Sync timed out or interrupted."
        )

        snapshot = integration.gsc_index_snapshots.filter(sync_status="complete").first()
        syncing = integration.gsc_index_snapshots.filter(sync_status="syncing").exists()

        # Refresh when missing or stale, unless a pass is already running.
        stale = snapshot is None or snapshot.created_at < timezone.now() - timedelta(hours=24)
        if stale and not syncing:
            start_gsc_index_sync(integration.id)
            syncing = True

        site_url = integration.metadata.get("site_url", "")

        from ..services.gsc import _coverage_key as _ckey

        # Live Search metrics to enrich indexed pages (best-effort).
        metrics: dict = {}
        date_start = date_end = ""
        try:
            from ..services.gsc import fetch_served_pages

            served = fetch_served_pages(integration)
            date_start, date_end = served["date_start"], served["date_end"]
            metrics = {_ckey(p["url"]): p for p in served["pages"]}
        except Exception as exc:  # noqa: BLE001 — metrics are optional enrichment
            logger.warning("GSC coverage: served metrics unavailable: %s", exc)

        if snapshot is None:
            # First load — inspection pass is running; show a verifying state.
            return Response(
                {
                    "property": site_url,
                    "sync_status": "syncing" if syncing else "pending",
                    "checked_at": "",
                    "date_start": date_start,
                    "date_end": date_end,
                    "indexed_count": 0,
                    "not_indexed_count": 0,
                    "checked_count": 0,
                    "sitemap_total": 0,
                    "submitted": 0,
                    "pages": [],
                    "not_indexed": [],
                }
            )

        indexed_pages = []
        not_indexed = []
        for p in snapshot.pages:
            if p.get("on_google"):
                m = metrics.get(_ckey(p["url"])) if metrics else None
                indexed_pages.append(
                    {
                        "url": p["url"],
                        "clicks": m["clicks"] if m else 0,
                        "impressions": m["impressions"] if m else 0,
                        "ctr": m["ctr"] if m else 0.0,
                        "position": m["position"] if m else 0.0,
                        "coverage_state": p.get("coverage_state", ""),
                    }
                )
            else:
                not_indexed.append(
                    {
                        "url": p["url"],
                        "reason": p.get("coverage_state", "") or "Not indexed",
                    }
                )
        indexed_pages.sort(key=lambda p: p["impressions"], reverse=True)

        return Response(
            {
                "property": site_url,
                "sync_status": "syncing" if syncing else snapshot.sync_status,
                "checked_at": snapshot.created_at.isoformat(),
                "date_start": date_start,
                "date_end": date_end,
                "indexed_count": snapshot.indexed_count,
                "not_indexed_count": snapshot.not_indexed_count,
                "checked_count": snapshot.checked_count,
                "sitemap_total": snapshot.sitemap_total,
                "submitted": snapshot.submitted,
                "pages": indexed_pages,
                "not_indexed": not_indexed,
            }
        )

class GSCSitemapsView(APIView):
    """GET /api/integrations/google-search-console/sitemaps/?email="""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

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
                provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Search Console not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            from ..services.gsc import fetch_gsc_sitemaps

            return Response(fetch_gsc_sitemaps(integration))
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.error("GSC sitemaps error: %s", exc)
            return Response(
                {"error": "Failed to load sitemaps."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

