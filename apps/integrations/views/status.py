"""Cross-provider connection status."""

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.models import Organization
from core.permissions.throttling import PollingThrottle

from ..models import (
    Integration,
)
from ..serializers import (
    IntegrationSerializer,
)
from ._shared import (
    _resolve_org,
)


class IntegrationStatusView(APIView):
    """GET /api/integrations/status/?email=&org_id="""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]  # high-frequency read for dashboard/sidebar state

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        org_id = request.query_params.get("org_id")
        org_id = int(org_id) if org_id and org_id.isdigit() else None

        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Read-only: a status poll must never create an org (see _get_org_or_400).
        # A brand-new user simply has no org yet → no integrations.
        if org_id:
            org, err = _resolve_org(email, org_id)
            if err:
                return err
        else:
            org = (
                Organization.objects.filter(owner_email=email).order_by("id").first()
            )
        if org is None:
            return Response([])

        integrations = Integration.objects.filter(organization=org)
        serializer = IntegrationSerializer(integrations, many=True)
        return Response(serializer.data)

