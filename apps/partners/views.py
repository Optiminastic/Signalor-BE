"""Partner / affiliate program REST endpoints."""
from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Partner
from .services import set_attribution


class PartnerTrackView(APIView):
    """POST /api/partners/track/ — body {code, landing_path?}.

    Lightweight click acknowledgement. We do not record the click as a separate
    row (yet) — the frontend mainly calls this to verify the code is valid
    before stashing it in localStorage.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        code = (request.data.get("code") or "").strip().upper()
        if not code:
            return Response({"valid": False}, status=200)

        partner = Partner.objects.filter(code=code).first()
        if not partner or partner.status == Partner.Status.TERMINATED:
            return Response({"valid": False}, status=200)

        return Response({"valid": True, "partner_name": partner.name or ""}, status=200)


class PartnerAttributeView(APIView):
    """POST /api/partners/attribute/ — body {code, email, landing_path?}.

    Called from the sign-up flow when the affiliate localStorage key is present.
    Last-click semantics: any new attribute call overwrites the existing row.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        code = (request.data.get("code") or "").strip().upper()
        email = (request.data.get("email") or "").strip().lower()
        landing_path = (request.data.get("landing_path") or "").strip()

        if not code or not email:
            return Response({"detail": "code and email required"}, status=400)

        attribution = set_attribution(email, code, landing_path=landing_path)
        if not attribution:
            return Response({"detail": "invalid or terminated code"}, status=400)

        return Response(
            {
                "partner_code": attribution.partner.code,
                "expires_at": attribution.expires_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )
