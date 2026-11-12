"""
Draft actions and the brand owner's decisions on them.

Every view here is scoped to the caller's own brand through
``programs.get_program`` (owner only). An action that belongs to another brand
answers exactly like one that does not exist.

Approving records a decision. It does not send anything: sending an approved
action is a separate step run by the scheduler.
"""

from rest_framework import status
from rest_framework.response import Response

from .models import ApprovalDecision, GrowthAction
from .serializers_control import (
    ActionDetailSerializer,
    ActionSerializer,
    DecisionRequestSerializer,
    EditRequestSerializer,
    ResolutionRequestSerializer,
)
from .services import programs
from .services.control import approvals, editing, resolution
from .views import GrowthBaseView

PAGE_SIZE = 50
NOT_FOUND = {"detail": "Action not found."}
PENDING_STATES = (GrowthAction.State.AWAITING_APPROVAL, GrowthAction.State.APPROVED)


def _program_or_404(request, org_slug: str):
    program = programs.get_program(request.user.email, org_slug)
    if program is None:
        return None, Response({"detail": "Program not found."}, status=status.HTTP_404_NOT_FOUND)
    return program, None


def _actions_for(program):
    return GrowthAction.objects.filter(organization=program.organization).select_related(
        "current_version", "task"
    )


class ApprovalListView(GrowthBaseView):
    """GET the drafts waiting on the owner (or approved and not sent yet), newest first."""

    def get(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        # Decisions are prefetched for each action's approval expiry.
        pending = (
            _actions_for(program).filter(state__in=PENDING_STATES).prefetch_related("decisions")[:PAGE_SIZE]
        )
        return Response({"actions": ActionSerializer(pending, many=True).data})


class ActionDetailView(GrowthBaseView):
    """GET one action with its exact draft and its decision history."""

    def get(self, request, org_slug: str, public_id: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        action = (
            _actions_for(program).prefetch_related("decisions__version").filter(public_id=public_id).first()
        )
        if action is None:
            return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return Response({"action": ActionDetailSerializer(action).data})


class ActionDecisionView(GrowthBaseView):
    """POST .../actions/<id>/approve/ or .../actions/<id>/reject/.

    The body names the version and content hash the owner reviewed. If the
    draft is no longer that one, the decision is refused with 409.
    """

    decision = ""

    def post(self, request, org_slug: str, public_id: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        payload = DecisionRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        decision_request = approvals.DecisionRequest(
            decision=self.decision,
            version_number=payload.validated_data["version"],
            payload_hash=payload.validated_data["payload_hash"],
            actor_email=request.user.email,
            reason=payload.validated_data["reason"],
        )
        try:
            action = approvals.decide(program, public_id, decision_request)
        except approvals.ActionNotFound:
            return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        except approvals.DecisionRefused as exc:
            return Response({"detail": str(exc), "code": exc.code}, status=status.HTTP_409_CONFLICT)
        action = _actions_for(program).prefetch_related("decisions__version").get(pk=action.pk)
        return Response({"action": ActionDetailSerializer(action).data})


class ActionResolveView(GrowthBaseView):
    """POST .../actions/<id>/resolve/: close an action whose send outcome is unknown.

    ``resolution`` is ``confirmed_sent`` or ``cancel``. Nothing is sent either way.
    """

    def post(self, request, org_slug: str, public_id: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        payload = ResolutionRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        resolution_request = resolution.ResolutionRequest(
            resolution=payload.validated_data["resolution"],
            version_number=payload.validated_data["version"],
            payload_hash=payload.validated_data["payload_hash"],
            actor_email=request.user.email,
            reason=payload.validated_data["reason"],
        )
        try:
            action = resolution.resolve(program, public_id, resolution_request)
        except approvals.ActionNotFound:
            return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        except approvals.DecisionRefused as exc:
            return Response({"detail": str(exc), "code": exc.code}, status=status.HTTP_409_CONFLICT)
        action = _actions_for(program).prefetch_related("decisions__version").get(pk=action.pk)
        return Response({"action": ActionDetailSerializer(action).data})


class ActionEditView(GrowthBaseView):
    """POST .../actions/<id>/edit/: save the owner's edit as the next version.

    The action goes back to awaiting approval; any earlier approval stops
    counting. A field that can't be edited, or invalid content, is a 400.
    """

    def post(self, request, org_slug: str, public_id: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        payload = EditRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        edit_request = editing.EditRequest(
            version_number=payload.validated_data["version"],
            payload_hash=payload.validated_data["payload_hash"],
            fields=payload.validated_data["fields"],
            actor_email=request.user.email,
        )
        try:
            action = editing.edit(program, public_id, edit_request)
        except approvals.ActionNotFound:
            return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        except approvals.DecisionRefused as exc:
            code = status.HTTP_400_BAD_REQUEST if exc.code == editing.INVALID else status.HTTP_409_CONFLICT
            return Response({"detail": str(exc), "code": exc.code}, status=code)
        action = _actions_for(program).prefetch_related("decisions__version").get(pk=action.pk)
        return Response({"action": ActionDetailSerializer(action).data})


APPROVE = ApprovalDecision.Decision.APPROVED
REJECT = ApprovalDecision.Decision.REJECTED
REVOKE = ApprovalDecision.Decision.REVOKED
