"""
Growth Agent API (``/api/growth/``).

Called only by the ``ai-agent-signalor`` Next.js server, never by browsers.
Every view authenticates the signed service request, requires a Pro/Max
plan, throttles per user, and scopes data to brands the caller owns.
"""

from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponseRedirect
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.accounts.subscription_utils import effective_plan_key
from core.permissions.throttling import GrowthAgentThrottle

from .analysis_tasks import (
    dispatch_chat_turn,
    dispatch_positioning_report,
    dispatch_preview,
    dispatch_task_run,
    dispatch_weekly_snapshot,
)
from .auth import GrowthServiceAuthentication, HasGrowthAgentPlan, has_growth_access
from .serializers import (
    AgentActionLogSerializer,
    AgentConversationSerializer,
    AgentMessageSerializer,
    AnalysisRunStatusSerializer,
    BrandSerializer,
    CreateProgramSerializer,
    GrowthCycleSerializer,
    GrowthProgramSerializer,
    OnboardBrandSerializer,
    PositioningReportSerializer,
    PostMessageSerializer,
    UpdateTaskSerializer,
    WeeklySnapshotSerializer,
)
from .services import autopilot, onboarding, programs
from .services.chat import conversation
from .services.execution import on_demand
from .services.onboarding import Outcome
from .services.outreach import connect as gmail_connect
from .services.outreach.gmail import GmailError
from .services.planning import plan as planning
from .services.positioning import report as positioning
from .services.tracking import tracker


class GrowthBaseView(APIView):
    authentication_classes = [GrowthServiceAuthentication]
    permission_classes = [HasGrowthAgentPlan]
    throttle_classes = [GrowthAgentThrottle]


class MeView(GrowthBaseView):
    """Who the caller is, whether they can use the agent, and their brands.

    Open to any signed-in Signalor user (not only Pro/Max) so the frontend can
    show an upgrade message instead of a bare 403.
    """

    permission_classes = []

    def get(self, request):
        email = request.user.email
        brands = programs.owned_organizations(email).select_related("growth_program")
        return Response(
            {
                "email": email,
                "plan": effective_plan_key(email),
                "eligible": has_growth_access(email),
                "brands": BrandSerializer(brands, many=True).data,
            }
        )


class ProgramListCreateView(GrowthBaseView):
    def post(self, request):
        payload = CreateProgramSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        email = request.user.email
        org = programs.get_owned_organization(email, payload.validated_data["org_slug"])
        if org is None:
            return Response({"detail": "Brand not found."}, status=status.HTTP_404_NOT_FOUND)
        program, created = programs.get_or_create_program(email, org)
        autopilot.advance_onboarding(program)  # report -> plan, with no human step
        program.refresh_from_db()
        code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(GrowthProgramSerializer(program).data, status=code)


class ProgramDetailView(GrowthBaseView):
    def get(self, request, org_slug: str):
        program = programs.get_program(request.user.email, org_slug)
        if program is None:
            return Response({"detail": "Program not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(GrowthProgramSerializer(program).data)


def _run_payload(run) -> dict | None:
    return AnalysisRunStatusSerializer(run).data if run is not None else None


class BrandOnboardView(GrowthBaseView):
    """Paste a link: reuse or create the brand, and make sure it has an analysis."""

    def post(self, request):
        payload = OnboardBrandSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        result = onboarding.onboard_from_url(request.user.email, payload.validated_data["url"])
        if result.outcome is Outcome.INVALID:
            return Response({"detail": result.message}, status=status.HTTP_400_BAD_REQUEST)
        if result.outcome is Outcome.BLOCKED:
            return Response({"detail": result.message}, status=status.HTTP_403_FORBIDDEN)
        code = status.HTTP_201_CREATED if result.created_brand else status.HTTP_200_OK
        body = {
            "brand": BrandSerializer(result.organization).data,
            "created_brand": result.created_brand,
            "analysis": _run_payload(result.run),
        }
        return Response(body, status=code)


class BrandAnalysisView(GrowthBaseView):
    """Latest analysis status for one of the caller's brands (polled by the chat)."""

    def get(self, request, org_slug: str):
        org = programs.get_owned_organization(request.user.email, org_slug)
        if org is None:
            return Response({"detail": "Brand not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"analysis": _run_payload(onboarding.latest_run(org))})


class PositioningReportView(GrowthBaseView):
    """GET the latest report; POST to build one from the latest completed analysis."""

    def _program_or_404(self, request, org_slug: str):
        return programs.get_program(request.user.email, org_slug)

    def get(self, request, org_slug: str):
        program = self._program_or_404(request, org_slug)
        if program is None:
            return Response({"detail": "Program not found."}, status=status.HTTP_404_NOT_FOUND)
        report = positioning.latest_report(program)
        return Response({"report": PositioningReportSerializer(report).data if report else None})

    def post(self, request, org_slug: str):
        program = self._program_or_404(request, org_slug)
        if program is None:
            return Response({"detail": "Program not found."}, status=status.HTTP_404_NOT_FOUND)
        report, needs_build = positioning.request_report(program)
        if report is None:
            return Response(
                {"detail": "This brand has no completed analysis yet."}, status=status.HTTP_409_CONFLICT
            )
        if needs_build:
            dispatch_positioning_report(report.id)
        code = status.HTTP_202_ACCEPTED if needs_build else status.HTTP_200_OK
        return Response({"report": PositioningReportSerializer(report).data}, status=code)


class PlanView(GrowthBaseView):
    """GET the active month plan; POST to create it from the latest positioning report."""

    def get(self, request, org_slug: str):
        program = programs.get_program(request.user.email, org_slug)
        if program is None:
            return Response({"detail": "Program not found."}, status=status.HTTP_404_NOT_FOUND)
        cycle = planning.active_cycle(program)
        return Response({"plan": GrowthCycleSerializer(cycle).data if cycle else None})

    def post(self, request, org_slug: str):
        program = programs.get_program(request.user.email, org_slug)
        if program is None:
            return Response({"detail": "Program not found."}, status=status.HTTP_404_NOT_FOUND)
        rebuild = bool(request.data.get("rebuild", False)) if isinstance(request.data, dict) else False
        try:
            cycle, created = planning.create_plan(program, rebuild=rebuild)
        except planning.PlanError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response({"plan": GrowthCycleSerializer(cycle).data}, status=code)


class TaskUpdateView(GrowthBaseView):
    """PATCH a task to skip it or restore it. Only not-yet-started tasks can change."""

    def patch(self, request, org_slug: str, task_id: int):
        program = programs.get_program(request.user.email, org_slug)
        cycle = planning.active_cycle(program) if program else None
        if cycle is None:
            return Response({"detail": "Plan not found."}, status=status.HTTP_404_NOT_FOUND)
        payload = UpdateTaskSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        task = planning.set_task_status(cycle, task_id, payload.validated_data["status"])
        if task is None:
            return Response(
                {"detail": "Task not found or already started."}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(GrowthCycleSerializer(cycle).data)


def _tracking_payload(cycle) -> dict:
    snapshots = cycle.snapshots.order_by("started_at")
    return {
        "baseline_share_of_voice": cycle.baseline_share_of_voice,
        "target_share_of_voice": cycle.target_share_of_voice,
        "starts_on": cycle.starts_on,
        "snapshots": WeeklySnapshotSerializer(snapshots, many=True).data,
    }


class TrackingView(GrowthBaseView):
    """GET visibility progress for the active plan; POST to re-check now (once a day)."""

    def _cycle(self, request, org_slug: str):
        program = programs.get_program(request.user.email, org_slug)
        return planning.active_cycle(program) if program else None

    def get(self, request, org_slug: str):
        cycle = self._cycle(request, org_slug)
        if cycle is None:
            return Response({"detail": "Plan not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(_tracking_payload(cycle))

    def post(self, request, org_slug: str):
        cycle = self._cycle(request, org_slug)
        if cycle is None:
            return Response({"detail": "Plan not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            snapshot, needs_run = tracker.request_manual_snapshot(cycle)
        except tracker.TrackingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        if needs_run:
            dispatch_weekly_snapshot(snapshot.id)
        code = status.HTTP_202_ACCEPTED if needs_run else status.HTTP_200_OK
        return Response(_tracking_payload(cycle), status=code)


ACTIVITY_PAGE_SIZE = 50


class ActivityView(GrowthBaseView):
    """GET the agent's latest actions (read-only view of the append-only audit log)."""

    def get(self, request, org_slug: str):
        program = programs.get_program(request.user.email, org_slug)
        if program is None:
            return Response({"detail": "Program not found."}, status=status.HTTP_404_NOT_FOUND)
        logs = program.action_logs.select_related("task").order_by("-created_at")[:ACTIVITY_PAGE_SIZE]
        return Response({"activity": AgentActionLogSerializer(logs, many=True).data})


def _thread_payload(conv) -> list:
    return AgentMessageSerializer(conversation.thread(conv), many=True).data


def _program_response(request, org_slug: str):
    """(program, None) or (None, 404 response) for the caller's brand."""
    program = programs.get_program(request.user.email, org_slug)
    if program is None:
        return None, Response({"detail": "Program not found."}, status=status.HTTP_404_NOT_FOUND)
    return program, None


class ConversationListView(GrowthBaseView):
    """GET the brand's saved chats; POST a first message to start a new one."""

    def get(self, request, org_slug: str):
        program, missing = _program_response(request, org_slug)
        if missing:
            return missing
        conversations = conversation.list_conversations(program)
        return Response({"conversations": AgentConversationSerializer(conversations, many=True).data})

    def post(self, request, org_slug: str):
        program, missing = _program_response(request, org_slug)
        if missing:
            return missing
        payload = PostMessageSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            conv, reply = conversation.start_conversation(program, payload.validated_data["message"])
        except conversation.ChatError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        dispatch_chat_turn(reply.id)
        body = {"conversation": AgentConversationSerializer(conv).data, "messages": _thread_payload(conv)}
        return Response(body, status=status.HTTP_202_ACCEPTED)


class ConversationChatView(GrowthBaseView):
    """GET one chat's messages; POST a message into it (the reply is filled in the background)."""

    def _conversation(self, request, org_slug: str, public_id: str):
        program, missing = _program_response(request, org_slug)
        if missing:
            return None, missing
        conv = conversation.get_conversation(program, public_id)
        if conv is None:
            return None, Response({"detail": "Chat not found."}, status=status.HTTP_404_NOT_FOUND)
        return conv, None

    def get(self, request, org_slug: str, public_id: str):
        conv, missing = self._conversation(request, org_slug, public_id)
        if missing:
            return missing
        return Response({"messages": _thread_payload(conv)})

    def post(self, request, org_slug: str, public_id: str):
        conv, missing = self._conversation(request, org_slug, public_id)
        if missing:
            return missing
        payload = PostMessageSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            reply = conversation.post_message(conv, payload.validated_data["message"])
        except conversation.ChatError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        dispatch_chat_turn(reply.id)
        return Response({"messages": _thread_payload(conv)}, status=status.HTTP_202_ACCEPTED)


class GmailConnectionView(GrowthBaseView):
    """POST returns the Google consent URL; DELETE disconnects Gmail for this brand."""

    def post(self, request, org_slug: str):
        program = programs.get_program(request.user.email, org_slug)
        if program is None:
            return Response({"detail": "Program not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"auth_url": gmail_connect.start(program, request.user.email)})

    def delete(self, request, org_slug: str):
        program = programs.get_program(request.user.email, org_slug)
        if program is None:
            return Response({"detail": "Program not found."}, status=status.HTTP_404_NOT_FOUND)
        gmail_connect.disconnect(program)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _app_redirect(**params: str) -> HttpResponseRedirect:
    return HttpResponseRedirect(f"{settings.GROWTH_AGENT_APP_URL.rstrip('/')}/?{urlencode(params)}")


class GmailCallbackView(APIView):
    """Google redirects the browser here. Trust comes from the signed, expiring
    OAuth state (not the service signature), re-checked against brand ownership."""

    authentication_classes = []
    permission_classes = []
    throttle_classes = [AnonRateThrottle]

    def get(self, request):
        if request.query_params.get("error"):
            return _app_redirect(connect_error="gmail_denied")
        try:
            gmail_connect.finish(request.query_params.get("code", ""), request.query_params.get("state", ""))
        except gmail_connect.ConnectError as exc:
            return _app_redirect(connect_error=f"gmail_{exc}")
        except GmailError:
            return _app_redirect(connect_error="gmail_token_exchange")
        return _app_redirect(connected="gmail")


class TaskActionView(GrowthBaseView):
    """POST .../tasks/<id>/preview/ or .../tasks/<id>/run/ for a task in the active plan."""

    action = ""

    def post(self, request, org_slug: str, task_id: int):
        program = programs.get_program(request.user.email, org_slug)
        cycle = planning.active_cycle(program) if program else None
        task = cycle.tasks.filter(pk=task_id).first() if cycle else None
        if task is None:
            return Response({"detail": "Task not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            if self.action == "preview":
                on_demand.request_preview(task)
                dispatch_preview(task.id)
            else:
                on_demand.schedule_now(task)
                dispatch_task_run(task.id)
        except on_demand.OnDemandError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(GrowthCycleSerializer(cycle).data, status=status.HTTP_202_ACCEPTED)
