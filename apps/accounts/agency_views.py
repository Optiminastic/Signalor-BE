"""Agency team-management endpoints (role + members).

Email-based + AllowAny to match every other endpoint in this codebase; the
caller's role is re-derived server-side via ``agency_utils`` and enforced here —
the request never gets to name its own role.
"""

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .agency_utils import MAX_AGENCY_MEMBERS, get_agency_context
from .models import AgencyMembership


def _member_payload(m: AgencyMembership) -> dict:
    return {
        "id": m.id,
        "member_email": m.member_email,
        "role": m.role,
        "status": m.status,
    }


def _require_admin(request):
    """Return (context, error_response). Exactly one is non-None."""
    email = (
        request.query_params.get("email") if request.method == "GET" else request.data.get("email")
    ) or ""
    ctx = get_agency_context(email)
    if ctx is None:
        return None, Response(
            {"detail": "No agency found for this account.", "code": "not_agency"},
            status=status.HTTP_403_FORBIDDEN,
        )
    if not ctx.is_admin:
        return None, Response(
            {"detail": "Only an agency admin can manage the team.", "code": "forbidden"},
            status=status.HTTP_403_FORBIDDEN,
        )
    return ctx, None


class AgencyRoleView(APIView):
    """GET role/?email= → the caller's agency + role (or null)."""

    permission_classes = [AllowAny]

    def get(self, request):
        ctx = get_agency_context(request.query_params.get("email"))
        if ctx is None:
            return Response({"agency_email": None, "role": None})
        return Response({"agency_email": ctx.agency_email, "role": ctx.role})


class AgencyMemberListView(APIView):
    """GET members/?email= (admin) and POST members/invite/ (admin)."""

    permission_classes = [AllowAny]

    def get(self, request):
        ctx, err = _require_admin(request)
        if err:
            return err
        members = AgencyMembership.objects.filter(agency_email=ctx.agency_email).order_by("created_at")
        return Response([_member_payload(m) for m in members])

    def post(self, request):
        ctx, err = _require_admin(request)
        if err:
            return err

        member_email = (request.data.get("member_email") or "").strip().lower()
        if not member_email or "@" not in member_email:
            return Response(
                {"detail": "A valid teammate email is required.", "code": "invalid_email"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if member_email == ctx.agency_email:
            return Response(
                {"detail": "You are already the agency admin.", "code": "self_invite"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        role = request.data.get("role")
        if role not in dict(AgencyMembership.Role.choices):
            role = AgencyMembership.Role.MEMBER

        existing = AgencyMembership.objects.filter(
            agency_email=ctx.agency_email, member_email=member_email
        ).first()
        if existing is not None:
            return Response(
                {"detail": "This teammate is already on your team.", "code": "already_member"},
                status=status.HTTP_409_CONFLICT,
            )

        if AgencyMembership.objects.filter(agency_email=ctx.agency_email).count() >= MAX_AGENCY_MEMBERS:
            return Response(
                {
                    "detail": f"Your team is full ({MAX_AGENCY_MEMBERS} members max). "
                    "Remove a member to add another.",
                    "code": "team_full",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        membership = AgencyMembership.objects.create(
            agency_email=ctx.agency_email,
            member_email=member_email,
            role=role,
            status=AgencyMembership.Status.ACTIVE,
            invited_by=ctx.agency_email,
        )
        return Response(_member_payload(membership), status=status.HTTP_201_CREATED)


class AgencyMemberDetailView(APIView):
    """PATCH members/<id>/ (change role) and DELETE members/<id>/ (remove)."""

    permission_classes = [AllowAny]

    def _get_owned(self, request, member_id):
        ctx, err = _require_admin(request)
        if err:
            return None, None, err
        membership = AgencyMembership.objects.filter(pk=member_id, agency_email=ctx.agency_email).first()
        if membership is None:
            return (
                None,
                None,
                Response(
                    {"detail": "Team member not found.", "code": "not_found"},
                    status=status.HTTP_404_NOT_FOUND,
                ),
            )
        return ctx, membership, None

    def patch(self, request, member_id):
        _ctx, membership, err = self._get_owned(request, member_id)
        if err:
            return err
        role = request.data.get("role")
        if role in dict(AgencyMembership.Role.choices):
            membership.role = role
        membership.save()
        return Response(_member_payload(membership))

    def delete(self, request, member_id):
        _ctx, membership, err = self._get_owned(request, member_id)
        if err:
            return err
        membership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
