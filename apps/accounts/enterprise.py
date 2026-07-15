"""Enterprise 'Contact Sales' lead capture.

POST /api/enterprise/lead/ — public (AllowAny), throttled. Persists the lead to
the EnterpriseLead table (nothing gets lost) AND best-effort emails the sales
inbox so the team is notified immediately. Email failure never fails the request
— the row is already saved.

All fields are untrusted client claims; the serializer validates shape and the
server stamps created_at / status.
"""

from __future__ import annotations

import logging
import os

from django.conf import settings
from django.core.mail import EmailMessage
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.throttling import AuthSendThrottle

from .models import EnterpriseLead

logger = logging.getLogger("apps")


def _sales_inbox() -> str:
    return (
        os.getenv("ENTERPRISE_SALES_EMAIL", "").strip()
        or getattr(settings, "DEFAULT_FROM_EMAIL", None)
        or "hello@signalor.ai"
    )


def _default_from() -> str:
    return getattr(settings, "DEFAULT_FROM_EMAIL", None) or "Signalor <billing@signalor.ai>"


class EnterpriseLeadSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnterpriseLead
        fields = [
            "brand_name",
            "website",
            "email",
            "prompts_required",
            "brands_count",
            "current_investment",
            "support_level",
            "preferred_currency",
            "team_size",
            "ai_engines",
        ]

    def validate_brand_name(self, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise serializers.ValidationError("Brand name is required.")
        return cleaned

    def validate_ai_engines(self, value):
        # Accept a list of short engine names; coerce away anything weird.
        if not isinstance(value, list):
            return []
        return [str(v).strip().lower() for v in value if str(v).strip()][:20]


def _notify_sales(lead: EnterpriseLead) -> None:
    """Best-effort notification email to the sales inbox."""
    lines = [
        f"Brand: {lead.brand_name}",
        f"Website: {lead.website or '—'}",
        f"Contact email: {lead.email or '—'}",
        f"Prompts required: {lead.prompts_required if lead.prompts_required is not None else '—'}",
        f"Brands / domains: {lead.brands_count if lead.brands_count is not None else '—'}",
        f"Current SEO/content investment: {lead.current_investment or '—'}",
        f"Required support level: {lead.support_level or '—'}",
        f"Preferred currency: {lead.preferred_currency or '—'}",
        f"Team size: {lead.team_size or '—'}",
        f"AI engines to track: {', '.join(lead.ai_engines) if lead.ai_engines else '—'}",
        "",
        f"Submitted: {lead.created_at:%Y-%m-%d %H:%M UTC}",
    ]
    try:
        EmailMessage(
            subject=f"[Enterprise lead] {lead.brand_name}",
            body="\n".join(lines),
            from_email=_default_from(),
            to=[_sales_inbox()],
            reply_to=[lead.email] if lead.email else None,
        ).send(fail_silently=True)
    except Exception:
        logger.exception("enterprise: failed to send sales notification for lead id=%s", lead.id)


class EnterpriseLeadCreateView(APIView):
    """POST /api/enterprise/lead/ — capture an Enterprise 'Contact Sales' lead."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthSendThrottle]

    def post(self, request):
        serializer = EnterpriseLeadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lead = serializer.save()
        _notify_sales(lead)
        return Response(
            {"ok": True, "id": lead.id},
            status=status.HTTP_201_CREATED,
        )
