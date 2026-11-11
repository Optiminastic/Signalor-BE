import contextlib
import hashlib
import logging
from collections.abc import Mapping

from django.conf import settings
from django.db import connection, transaction
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analyzer.onboarding_security import gate_onboarding_endpoint
from core.auth.identity import resolve_request_email
from core.permissions.throttling import PollingThrottle

from . import access, observation
from .models import BrandProfile, Organization
from .serializers import (
    BrandProfileSerializer,
    OnboardSerializer,
    OrganizationSerializer,
    OrganizationUpdateSerializer,
)
from .throttling import OnboardEmailThrottle
from .utils import normalize_url

logger = logging.getLogger("apps")

# The whole edit allowlist for a brand. Derived from the update serializer so
# the two cannot disagree.
EDITABLE_FIELDS = frozenset(OrganizationUpdateSerializer.Meta.fields)

# Postgres advisory locks are keyed by bigint, so hash the email down to one.
# Signed 63-bit range — pg_advisory_xact_lock takes a signed bigint.
_LOCK_KEY_MASK = (1 << 63) - 1


@contextlib.contextmanager
def _serialize_brand_creation(email: str):
    """Serialize brand creation for one email so the plan cap can't be raced.

    The cap is a check-then-create, so two concurrent onboards could both read
    "0 brands, limit 1" and both insert. A transaction-scoped advisory lock
    keyed on the email makes the check and the insert atomic against each other
    without locking anything else.

    Postgres only. On sqlite (tests) this is a no-op: that backend serializes
    writes anyway and has no advisory-lock equivalent.
    """
    if connection.vendor != "postgresql":
        yield
        return
    digest = hashlib.sha256(email.encode("utf-8")).digest()
    key = int.from_bytes(digest[:8], "big") & _LOCK_KEY_MASK
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])
    yield


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

        # Only a genuinely new brand counts against the plan limit. The check
        # and the insert share one transaction + advisory lock so concurrent
        # onboards for the same email can't both pass a cap of N.
        with transaction.atomic(), _serialize_brand_creation(email):
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
    throttle_classes = [PollingThrottle]

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
    # Every dashboard page load resolves the active project through here, so an
    # explicit read scope is required: the DEFAULT_THROTTLE_RATES["anon"] fallback
    # of 60/hour is shared per IP with every other unscoped endpoint, and once a
    # run poll exhausted it the whole dashboard 429'd and hung on its spinner.
    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Agency teammates work inside the agency owner's brands, so surface the
        # agency's orgs ALONGSIDE any the caller owns personally. This used to
        # substitute the agency's email for the caller's, which both hid a
        # teammate's own brands and, because the context resolved to their own
        # agency first, showed an invitee nothing belonging to the team.
        from apps.accounts.agency_utils import readable_owner_emails

        orgs = Organization.objects.filter(owner_email__in=readable_owner_emails(email))
        return Response(OrganizationSerializer(orgs, many=True).data)


class OrganizationDetailView(APIView):
    """Edit or delete a brand (SEC-1a).

    A verified caller is always required here, whatever the global
    ``REQUIRE_VERIFIED_IDENTITY`` flag says. The brand id in the URL is a
    sequential integer, so identity can never come from the request itself.

    - Edit: the owner, or an admin of the agency that owns the brand.
    - Delete: the owner only.
    - Only ``name`` and ``url`` can be edited.

    A brand the caller may not touch answers exactly like one that does not
    exist, so the ids cannot be probed. Every request is also recorded by
    ``observation``, which alerts on the attempts that are refused.
    """

    # Identity and ownership are enforced in ``_refusal``. The DRF permission
    # stays open so an unverified caller gets this view's own 401 body.
    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def patch(self, request, pk):
        return self._handle(request, pk, observation.METHOD_PATCH, access.may_edit, self._update)

    def delete(self, request, pk):
        return self._handle(request, pk, observation.METHOD_DELETE, access.may_delete, self._destroy)

    def _handle(self, request, pk, method, is_allowed, operation):
        org = Organization.objects.filter(pk=pk).first()
        owner_change = False
        try:
            refusal = _refusal(request, org, is_allowed)
            # The body is read only for a caller who is allowed to edit, so an
            # unauthenticated request is refused before anything is parsed.
            if refusal is None and method == observation.METHOD_PATCH:
                owner_change = observation.owner_change_attempted(request)
            # Classified before the operation: after a delete the owner can't be read.
            seen = observation.observe(request, method, org, pk, owner_change)
            response = refusal if refusal is not None else operation(request, org)
        except APIException as exc:
            observation.record(observation.observe(request, method, org, pk, owner_change), exc.status_code)
            raise
        observation.record(seen, response.status_code)
        return response

    def _update(self, request, org):
        rejected = _fields_not_editable(request.data)
        if observation.OWNER_FIELD in rejected:
            return Response(
                {"detail": "The owner of a brand cannot be changed here.", "code": "owner_email_read_only"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if rejected:
            return Response(
                {
                    "detail": "These fields cannot be edited.",
                    "code": "field_not_editable",
                    "fields": rejected,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = OrganizationUpdateSerializer(org, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        logger.info(
            "organization updated org_id=%s fields=%s", org.pk, ",".join(sorted(serializer.validated_data))
        )
        return Response(OrganizationSerializer(org).data)

    def _destroy(self, request, org):
        org_id = org.pk
        org.delete()
        logger.info("organization deleted org_id=%s", org_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _refusal(request, org, is_allowed) -> Response | None:
    """Why this caller may not act on ``org``, or None when they may.

    Order matters. Identity is checked before the brand is considered, so an
    unverified caller learns nothing about which ids exist.
    """
    if not getattr(settings, "BETTER_AUTH_JWKS_URL", ""):
        # Distinct from 401: no caller can authenticate on this deployment, and
        # the operator should not go hunting for a bad token.
        logger.error(
            "Refusing %s %s: BETTER_AUTH_JWKS_URL is unset, so no caller can authenticate.",
            request.method,
            request.path,
        )
        return Response(
            {"detail": "Authentication is not configured on this server.", "code": "auth_unavailable"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    email, unauthenticated = resolve_request_email(request, require_verified=True)
    if unauthenticated is not None:
        return unauthenticated
    if org is None or not is_allowed(email, org):
        return _organization_not_found()
    return None


def _fields_not_editable(data) -> list[str]:
    """Keys in the request body that are outside the edit allowlist, sorted."""
    if not isinstance(data, Mapping):
        return []  # not an object: the serializer rejects it with its usual 400
    return sorted(str(key) for key in data if key not in EDITABLE_FIELDS)


def _organization_not_found() -> Response:
    return Response({"error": "Organization not found."}, status=status.HTTP_404_NOT_FOUND)


# ── Brand Profile (Epic 2) ────────────────────────────────────────────────
# Owner-scoped: resolves the org by its unguessable slug AND the caller's
# owner_email (via agency context), so possession of the slug alone is not
# enough to read/edit/approve a brand's profile (CLAUDE.md §5.3).


def _owned_org(slug: str, email: str, *, write: bool = False):
    """Resolve the org by slug, scoped to the brands ``email`` may reach.

    Returns the Organization, or None when missing / out of the caller's scope.
    ``write=True`` narrows the scope to agencies the caller ADMINISTERS, so an
    agency Member can read a brand profile without being able to rewrite or
    approve it. The roster legend's "View reports and work through assigned
    actions", enforced rather than merely described.
    """
    if not email:
        return None
    from apps.accounts.agency_utils import readable_owner_emails, writable_owner_emails

    owners = writable_owner_emails(email) if write else readable_owner_emails(email)
    return Organization.objects.filter(slug=slug, owner_email__in=owners).first()


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
        org = _owned_org(slug, email, write=True)
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
        org = _owned_org(slug, email, write=True)
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
