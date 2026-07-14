import logging

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analyzer.onboarding_security import gate_onboarding_endpoint

from .models import BrandProfile, Organization
from .serializers import BrandProfileSerializer, OnboardSerializer, OrganizationSerializer
from .throttling import OnboardEmailThrottle
from .utils import normalize_url

logger = logging.getLogger("apps")


class OnboardView(APIView):
    """Create an Organization for a brand-new onboarding session.

    Security gates (in order):
      1. Per-email throttle (5/hour) — limits damage even from a botnet
         that's rotating IPs to dodge the global per-IP middleware.
      2. ``X-Onboarding-Token`` single-use signed token from
         ``/api/analyzer/onboarding-start/`` (bypassed only for internal
         emails and active paying subscribers — never bypassed just because
         an org already exists for this email; see issue #16).
      3. Duplicate detection by (owner_email, normalized_url) — returns
         409 + the existing org so the FE can switch to it instead of
         creating a dupe. Checked BEFORE the plan limit: re-onboarding an
         existing domain creates nothing, so it must not 403.
      4. Plan limit (``project_limit_reached``) — only a genuinely new brand
         counts against it.
    """

    permission_classes = [AllowAny]
    throttle_classes = [OnboardEmailThrottle]

    def post(self, request):
        serializer = OnboardSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        url = serializer.validated_data.get("url", "")

        ok, reason = gate_onboarding_endpoint(request, email=email)
        if not ok:
            logger.warning("onboard gate fail email=%s reason=%s", email, reason)
            return Response(
                {"detail": "Onboarding session required.", "reason": reason},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        from apps.accounts.subscription_utils import plan_limit_error_response_dict, project_limit_reached

        # Duplicate detection FIRST: re-onboarding an existing domain creates no
        # new project, so it must not trip the plan limit. Return the existing
        # org so the FE can switch to it (see issue #16).
        normalized = normalize_url(url)
        if normalized:
            existing = (
                Organization.objects.filter(owner_email=email, normalized_url=normalized)
                .order_by("created_at")
                .first()
            )
            if existing is not None:
                logger.info(
                    "onboard dedup hit email=%s normalized_url=%s existing_id=%s",
                    email,
                    normalized,
                    existing.id,
                )
                return Response(
                    {
                        "detail": "An organization for this domain already exists.",
                        "organization": OrganizationSerializer(existing).data,
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        # Only a genuinely new brand counts against the plan limit.
        reached, msg = project_limit_reached(email)
        if reached:
            return Response(
                plan_limit_error_response_dict(msg),
                status=status.HTTP_403_FORBIDDEN,
            )

        org = serializer.save()
        logger.info("Organization created: %s for %s", org.name, email)

        return Response(
            OrganizationSerializer(org).data,
            status=status.HTTP_201_CREATED,
        )


class CheckOrganizationView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()

        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        exists = Organization.objects.filter(owner_email=email).exists()
        return Response({"exists": exists})


class OrganizationListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Agency teammates work inside the agency owner's brands, so surface the
        # agency's orgs (not just orgs they own — members own none).
        from apps.accounts.agency_utils import get_agency_context

        owner_email = email
        ctx = get_agency_context(email)
        if ctx is not None:
            owner_email = ctx.agency_email

        orgs = Organization.objects.filter(owner_email=owner_email)
        return Response(OrganizationSerializer(orgs, many=True).data)


class OrganizationDetailView(APIView):
    permission_classes = [AllowAny]

    def patch(self, request, pk):
        try:
            org = Organization.objects.get(pk=pk)
        except Organization.DoesNotExist:
            return Response(
                {"error": "Organization not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = OrganizationSerializer(org, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        try:
            org = Organization.objects.get(pk=pk)
        except Organization.DoesNotExist:
            return Response(
                {"error": "Organization not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        org.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Brand Profile (Epic 2) ────────────────────────────────────────────────
# Owner-scoped: resolves the org by its unguessable slug AND the caller's
# owner_email (via agency context), so possession of the slug alone is not
# enough to read/edit/approve a brand's profile (CLAUDE.md §5.3).


def _owned_org(slug: str, email: str):
    """Resolve the org by slug, scoped to the caller's effective owner_email.
    Returns the Organization, or None when missing / not owned by the caller."""
    if not email:
        return None
    from apps.accounts.agency_utils import get_agency_context

    ctx = get_agency_context(email)
    owner_email = ctx.agency_email if ctx is not None else email
    return Organization.objects.filter(slug=slug, owner_email=owner_email).first()


class BrandProfileView(APIView):
    """GET the org's brand profile; PATCH to edit its content sections."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        email = request.query_params.get("email", "").lower().strip()
        org = _owned_org(slug, email)
        if org is None:
            return Response({"error": "Organization not found."}, status=status.HTTP_404_NOT_FOUND)
        profile = BrandProfile.objects.filter(organization=org).first()
        if profile is None:
            return Response({}, status=status.HTTP_200_OK)  # no profile bootstrapped yet
        return Response(BrandProfileSerializer(profile).data)

    def patch(self, request, slug):
        email = (request.data.get("email") or "").lower().strip()
        org = _owned_org(slug, email)
        if org is None:
            return Response({"error": "Organization not found."}, status=status.HTTP_404_NOT_FOUND)
        profile = BrandProfile.objects.filter(organization=org).first()
        if profile is None:
            return Response({"error": "No brand profile to edit."}, status=status.HTTP_404_NOT_FOUND)
        serializer = BrandProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class BrandProfileReviewView(APIView):
    """POST {decision: "approve"|"reject"} to transition status + stamp last_verified_at."""

    permission_classes = [AllowAny]

    def post(self, request, slug):
        email = (request.data.get("email") or "").lower().strip()
        org = _owned_org(slug, email)
        if org is None:
            return Response({"error": "Organization not found."}, status=status.HTTP_404_NOT_FOUND)

        decision = (request.data.get("decision") or "").lower().strip()
        mapping = {
            "approve": BrandProfile.Status.APPROVED,
            "reject": BrandProfile.Status.REJECTED,
        }
        if decision not in mapping:
            return Response(
                {"error": "decision must be 'approve' or 'reject'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile = BrandProfile.objects.filter(organization=org).first()
        if profile is None:
            return Response({"error": "No brand profile to review."}, status=status.HTTP_404_NOT_FOUND)

        from django.utils import timezone

        profile.status = mapping[decision]
        profile.last_verified_at = timezone.now()
        profile.save(update_fields=["status", "last_verified_at", "updated_at"])
        return Response(BrandProfileSerializer(profile).data)
