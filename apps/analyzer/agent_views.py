"""Growth Agent API — today's ranked task plan for a brand.

A read model over ``UserAction`` (see ``agent_plan``). Unlike the other
``runs/s/<slug>/`` endpoints, these are ownership-checked rather than AllowAny:
the plan is per-brand and the same identity model (``?email=``) is used to verify
the caller owns the run's organization.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.agency_utils import get_agency_context
from core.throttling import ExpensiveThrottle, PollingThrottle

from .agent_plan import build_agent_plan, mark_refreshed, refresh_available_at
from .models import AnalysisRun


def _resolve_owned_run(slug: str, email: str):
    """Return (run, owner_email) if ``email`` owns the run's org, else (None, None)."""
    run = get_object_or_404(AnalysisRun, slug=slug)
    ctx = get_agency_context(email)
    owner_email = ctx.agency_email if ctx else email
    if not (run.organization and run.organization.owner_email == owner_email):
        return None, None
    return run, owner_email


class AgentPlanView(APIView):
    """GET /api/analyzer/runs/s/<slug>/agent/plan/?email= → today's ranked plan."""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        email = (request.query_params.get("email") or "").lower().strip()
        if not email:
            return Response({"error": "email is required."}, status=status.HTTP_400_BAD_REQUEST)

        run, owner_email = _resolve_owned_run(slug, email)
        if run is None:
            return Response(
                {"detail": "Brand not found for this account.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        plan = build_agent_plan(run, owner_email, today=timezone.now().date())
        return Response(plan)


class AgentPlanRefreshView(APIView):
    """POST /api/analyzer/runs/s/<slug>/agent/plan/refresh/ {email}

    Re-materialize the run's recommendations into tasks and re-rank them now,
    instead of waiting for the nightly job. Does NOT touch the user's live site —
    the per-row CTAs handle any actual fixes through the deliberate auto-fix flow.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        email = (request.data.get("email") or "").lower().strip()
        if not email:
            return Response({"error": "email is required."}, status=status.HTTP_400_BAD_REQUEST)

        run, owner_email = _resolve_owned_run(slug, email)
        if run is None:
            return Response(
                {"detail": "Brand not found for this account.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Once per 24h per brand — the plan is a daily artifact.
        next_at = refresh_available_at(run)
        if next_at is not None:
            return Response(
                {
                    "detail": "You can refresh the plan once a day. Try again later.",
                    "code": "rate_limited",
                    "refresh_available_at": next_at.isoformat(),
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        from .action_sync import materialize_run_actions
        from .pipeline.recommendations import reprioritize_run_recommendations

        materialize_run_actions(run, owner_email)
        try:
            reprioritize_run_recommendations(run)
        except Exception:
            # Ranking is best-effort; the plan falls back to raw priority.
            pass

        mark_refreshed(run)
        plan = build_agent_plan(run, owner_email, today=timezone.now().date())
        return Response(plan)
