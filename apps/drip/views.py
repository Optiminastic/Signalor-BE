import logging

from django.core import signing
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PricingDripState
from .serializers import CheckoutStartedSerializer, PricingViewedSerializer
from .unsubscribe import unsign_email

logger = logging.getLogger("apps")


# Merge-tag-bearing field names. Updated on every pricing_viewed ping (any
# non-empty value wins; empty/null values leave the existing value intact so a
# pre-existing geo_score doesn't get wiped by a later visit without one).
_MERGE_FIELDS = (
    "first_name",
    "domain",
    "geo_score",
    "fix_count",
    "top_competitor",
    "competitor_list",
    "cms_platform",
    "top_recommendation_title",
    "issue_count",
    "competitor_count",
)


@method_decorator(csrf_exempt, name="dispatch")
class PricingViewedView(APIView):
    """POST /api/drip/pricing-viewed/ — enrol the user into the drip cohort.

    Idempotent: subsequent visits refresh merge-tag fields but never reset
    `entered_at` or `current_step`.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = PricingViewedSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        email = data["email"].lower().strip()

        state, created = PricingDripState.objects.get_or_create(email=email)

        if state.suppressed:
            # User already checked out / purchased — do not re-enrol.
            return Response({"enrolled": False, "suppressed": True}, status=status.HTTP_200_OK)

        update_fields = []
        amplitude_user_id = data.get("amplitude_user_id", "")
        if amplitude_user_id and amplitude_user_id != state.amplitude_user_id:
            state.amplitude_user_id = amplitude_user_id
            update_fields.append("amplitude_user_id")

        for field in _MERGE_FIELDS:
            value = data.get(field)
            if value in (None, ""):
                continue
            if getattr(state, field) == value:
                continue
            setattr(state, field, value)
            update_fields.append(field)

        if update_fields:
            update_fields.append("updated_at")
            state.save(update_fields=update_fields)

        return Response(
            {
                "enrolled": True,
                "created": created,
                "current_step": state.current_step,
                "entered_at": state.entered_at,
            },
            status=status.HTTP_200_OK,
        )


@method_decorator(csrf_exempt, name="dispatch")
class CheckoutStartedView(APIView):
    """POST /api/drip/checkout-started/ — pull the user out of the drip.

    Creates a suppressed row if none exists, so the user is permanently
    excluded even if they later revisit /pricing.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = CheckoutStartedSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].lower().strip()

        state, _ = PricingDripState.objects.get_or_create(email=email)
        if not state.suppressed:
            state.suppressed = True
            state.suppressed_reason = "checkout_started"
            state.suppressed_at = timezone.now()
            state.save(update_fields=["suppressed", "suppressed_reason", "suppressed_at", "updated_at"])
            logger.info("Drip suppressed for %s (reason=checkout_started)", email)

        return Response({"suppressed": True}, status=status.HTTP_200_OK)


@method_decorator(csrf_exempt, name="dispatch")
class UnsubscribeView(View):
    """GET/POST /api/drip/unsubscribe/?token=... — recipient-facing opt-out.

    GET shows a confirmation page (also serves Gmail/Apple Mail link prefetch
    gracefully — the unsubscribe still happens). POST is the endpoint Gmail's
    "One-Click Unsubscribe" feature hits per RFC 8058 (List-Unsubscribe-Post).
    Both flip `suppressed=True` and return a confirmation.
    """

    def _suppress_via_token(self, token: str) -> str | None:
        """Return the unsubscribed email, or None if the token is invalid."""
        if not token:
            return None
        try:
            email = unsign_email(token)
        except signing.BadSignature:
            logger.warning("Drip unsubscribe rejected: bad signature")
            return None

        state, _ = PricingDripState.objects.get_or_create(email=email)
        if not state.suppressed:
            state.suppressed = True
            state.suppressed_reason = "user_unsubscribed"
            state.suppressed_at = timezone.now()
            state.save(update_fields=[
                "suppressed", "suppressed_reason", "suppressed_at", "updated_at",
            ])
            logger.info("Drip unsubscribed for %s (via email link)", email)
        return email

    def get(self, request):
        token = request.GET.get("token", "")
        email = self._suppress_via_token(token)
        if email is None:
            return HttpResponse(
                "<h1>Invalid unsubscribe link</h1>"
                "<p>This link is expired or malformed. If you keep receiving "
                "emails, reply <strong>STOP</strong> to any message.</p>",
                status=400,
            )
        return render(request, "drip/unsubscribed.html", {"email": email})

    def post(self, request):
        # Gmail's One-Click feature posts to this URL with a tiny form body.
        # We accept the token from either the query string or the form data.
        token = request.GET.get("token") or request.POST.get("token", "")
        if self._suppress_via_token(token) is None:
            return HttpResponse(status=400)
        return HttpResponse(status=200)
