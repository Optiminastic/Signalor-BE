"""WooCommerce: connect, sync, data."""

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions.throttling import ExpensiveThrottle

from ..models import (
    Integration,
)
from ..serializers import (
    IntegrationSerializer,
    WooCommerceConnectSerializer,
    WooCommerceDataSnapshotSerializer,
)
from ._shared import (
    _resolve_org,
)


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

        from ..services.woocommerce import validate_woocommerce_connection

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

        return Response(
            {
                "message": "WooCommerce connected successfully.",
                "integration": IntegrationSerializer(integration).data,
            }
        )

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
    throttle_classes = [ExpensiveThrottle]

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

        from ..tasks import start_woocommerce_sync

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

        # Clear any dead "syncing" row (killed worker) so it can't wedge auto-sync.
        from ..sync_health import reap_stale_syncing

        reap_stale_syncing(integration.woocommerce_snapshots)

        stale_threshold = timezone.now() - timedelta(hours=24)
        if (
            snapshot.created_at < stale_threshold
            and snapshot.sync_status == "complete"
            and not integration.woocommerce_snapshots.filter(sync_status="syncing").exists()
        ):
            from ..tasks import start_woocommerce_sync

            start_woocommerce_sync(integration.id)

        serializer = WooCommerceDataSnapshotSerializer(snapshot)
        return Response(serializer.data)

