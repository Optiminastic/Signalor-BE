"""
Draft actions and the brand owner's decisions on them.

Every view here is scoped to the caller's own brand through
``programs.get_program`` (owner only). An action that belongs to another brand
answers exactly like one that does not exist.

Approving records a decision. It does not send anything: sending an approved
action is a separate step run by the scheduler.
"""

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from .analysis_tasks import run_growth_task
from .models import AgentActionLog, ApprovalDecision, GrowthAction, GrowthTask, OutreachContact
from .serializers_control import (
    ActionDetailSerializer,
    ActionSerializer,
    DecisionRequestSerializer,
    EditRequestSerializer,
    OwnerContactSerializer,
    ResolutionRequestSerializer,
)
from .services import programs
from .services.control import approvals, editing, resolution
from .services.execution import audit
from .views import GrowthBaseView

PAGE_SIZE = 50
NOT_FOUND = {"detail": "Action not found."}
PENDING_STATES = (GrowthAction.State.AWAITING_APPROVAL, GrowthAction.State.APPROVED)


def _program_or_404(request, org_slug: str):
    program = programs.get_program(request.user.email, org_slug)
    if program is None:
        return None, Response({"detail": "Program not found."}, status=status.HTTP_404_NOT_FOUND)
    return program, None


def _detail_queryset(program):
    """Actions with everything the detail view reads, in a fixed number of queries."""
    return _actions_for(program).prefetch_related("decisions__version", "versions", "execution_events")


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
        action = _detail_queryset(program).filter(public_id=public_id).first()
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
        action = _detail_queryset(program).get(pk=action.pk)
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
        action = _detail_queryset(program).get(pk=action.pk)
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
        action = _detail_queryset(program).get(pk=action.pk)
        return Response({"action": ActionDetailSerializer(action).data})


NOT_WAITING = {"detail": "This task isn't waiting for a contact.", "code": "illegal_state"}
OPTED_OUT = {"detail": "That contact opted out of emails.", "code": "opted_out"}
OWNER_SOURCE = "Added by the brand owner"


def _task_waiting_for_contact(program, task_id: int) -> GrowthTask | None:
    return (
        GrowthTask.objects.select_related("cycle__program__organization")
        .filter(
            pk=task_id,
            cycle__program=program,
            status=GrowthTask.Status.AWAITING_APPROVAL,
            result_payload__has_key="needs_contact",
        )
        .first()
    )


def _skip(task: GrowthTask, actor: str) -> Response:
    task.status = GrowthTask.Status.SKIPPED
    task.result_payload = {**task.result_payload, "skipped": "Skipped by the brand owner: no contact."}
    task.save(update_fields=["status", "result_payload", "updated_at"])
    audit.record(
        task.cycle.program, task, "contact_skipped", AgentActionLog.Outcome.SUCCEEDED, {}, actor_email=actor
    )
    return Response({"task": {"id": task.id, "status": task.status}})


def _use_contact(task: GrowthTask, email: str, actor: str) -> Response:
    org = task.cycle.program.organization
    contact, _ = OutreachContact.objects.get_or_create(
        organization=org, email=email, defaults={"domain": email.split("@")[-1], "source_url": OWNER_SOURCE}
    )
    if contact.opted_out:
        return Response(OPTED_OUT, status=status.HTTP_409_CONFLICT)
    needed = task.result_payload.get("needs_contact", {})
    task.status = GrowthTask.Status.PLANNED
    task.scheduled_for = timezone.now().date()
    task.result_payload = {"owner_contact_id": contact.id, "contact_for": needed.get("domain", "")}
    task.save(update_fields=["status", "scheduled_for", "result_payload", "updated_at"])
    detail = {"domain": needed.get("domain", ""), "contact_domain": contact.domain}
    audit.record(
        task.cycle.program, task, "contact_added", AgentActionLog.Outcome.SUCCEEDED, detail, actor_email=actor
    )
    run_growth_task.delay(task.id)  # the agent writes the pitch for this contact
    return Response({"task": {"id": task.id, "status": task.status}}, status=status.HTTP_202_ACCEPTED)


class TaskContactView(GrowthBaseView):
    """POST .../tasks/<id>/contact/ when no contact email was found for an outreach task.

    ``{"email": ...}``: the agent writes the pitch for that contact (202, queued).
    ``{"skip": true}``: the task is skipped. Either way it's the owner's call.
    """

    def post(self, request, org_slug: str, task_id: int):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        payload = OwnerContactSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        task = _task_waiting_for_contact(program, task_id)
        if task is None:
            return Response(NOT_WAITING, status=status.HTTP_409_CONFLICT)
        if payload.validated_data["skip"]:
            return _skip(task, request.user.email)
        return _use_contact(task, payload.validated_data["email"].lower(), request.user.email)


APPROVE = ApprovalDecision.Decision.APPROVED
REJECT = ApprovalDecision.Decision.REJECTED
REVOKE = ApprovalDecision.Decision.REVOKED
