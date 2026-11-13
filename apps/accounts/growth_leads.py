"""Public 'Let's get your website growing' lead capture.

POST /api/growth-leads/ — public (AllowAny), throttled per IP. Persists the
enquiry to the GrowthLead table AND best-effort emails the sales inbox so the
team is notified. Email failure never fails the request — the row is saved.

Mirrors the EnterpriseLead flow in ``enterprise.py``. All fields are untrusted
client claims: the serializer validates and normalises them, and the server
stamps lead_id / created_at / status / services.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import EmailMessage
from django.core.validators import URLValidator
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions.throttling import AuthSendThrottle

from .enterprise import _default_from, _sales_inbox
from .models import GrowthLead

logger = logging.getLogger("apps")

ALL_SERVICES = [GrowthLead.Service.SEO, GrowthLead.Service.GEO, GrowthLead.Service.AEO]

_url_validator = URLValidator(schemes=["http", "https"])


def normalize_website(raw: str) -> str:
    """Accept 'acme.com' or 'https://acme.com/'; return a validated http(s) URL.

    Raises DjangoValidationError if the result isn't a public-looking URL.
    """
    value = (raw or "").strip()
    if not value:
        raise DjangoValidationError("Website URL is required.")
    if "://" not in value:
        value = f"https://{value}"
    _url_validator(value)
    host = urlsplit(value).hostname or ""
    # URLValidator accepts "localhost" and bare IPs; a lead form wants a domain.
    if "." not in host or host.replace(".", "").isdigit():
        raise DjangoValidationError("Enter a valid website URL.")
    return value


def expand_services(service: str) -> list[str]:
    """Map the single dropdown choice to the list of services it covers."""
    if service == GrowthLead.Service.ALL:
        return [str(s) for s in ALL_SERVICES]
    return [service]


class GrowthLeadCreateSerializer(serializers.ModelSerializer):
    """Request schema. Only these six fields are writable by the client."""

    full_name = serializers.CharField(max_length=120, trim_whitespace=True)
    company_name = serializers.CharField(max_length=200, trim_whitespace=True)
    website = serializers.CharField(max_length=500, trim_whitespace=True)
    goals = serializers.CharField(
        max_length=2000, required=False, allow_blank=True, trim_whitespace=True, default=""
    )

    class Meta:
        model = GrowthLead
        fields = ["full_name", "email", "company_name", "website", "service", "goals"]
        extra_kwargs = {
            "email": {"error_messages": {"invalid": "Enter a valid work email."}},
            "service": {"error_messages": {"invalid_choice": "Choose what you'd like to improve."}},
        }

    def validate_full_name(self, value: str) -> str:
        if len(value) < 2:
            raise serializers.ValidationError("Enter your full name.")
        return value

    def validate_email(self, value: str) -> str:
        return value.strip().lower()

    def validate_website(self, value: str) -> str:
        try:
            return normalize_website(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError("Enter a valid website URL, e.g. acme.com.") from exc

    def create(self, validated_data: dict) -> GrowthLead:
        validated_data["services"] = expand_services(validated_data["service"])
        return super().create(validated_data)


class GrowthLeadResponseSerializer(serializers.ModelSerializer):
    """Response schema — never echo the full row back to an anonymous caller."""

    class Meta:
        model = GrowthLead
        fields = ["lead_id", "status", "created_at"]


def _notify_sales(lead: GrowthLead) -> None:
    """Best-effort notification email to the sales inbox."""
    lines = [
        f"Name: {lead.full_name}",
        f"Email: {lead.email}",
        f"Company / brand: {lead.company_name}",
        f"Website: {lead.website}",
        f"Wants to improve: {lead.get_service_display()}",
        "",
        "Goals:",
        lead.goals or "—",
        "",
        f"Lead ID: {lead.lead_id}",
        f"Submitted: {lead.created_at:%Y-%m-%d %H:%M UTC}",
    ]
    try:
        EmailMessage(
            subject=f"[Growth lead] {lead.company_name}",
            body="\n".join(lines),
            from_email=_default_from(),
            to=[_sales_inbox()],
            reply_to=[lead.email],
        ).send(fail_silently=True)
    except Exception:
        logger.exception("growth_leads: failed to send sales notification for lead=%s", lead.lead_id)


class GrowthLeadCreateView(APIView):
    """POST /api/growth-leads/ — capture a website-growth enquiry."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthSendThrottle]

    def post(self, request):
        serializer = GrowthLeadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lead = serializer.save()
        _notify_sales(lead)
        body = GrowthLeadResponseSerializer(lead).data
        return Response({"ok": True, **body}, status=status.HTTP_201_CREATED)
