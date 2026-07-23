import logging
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.throttling import ExpensiveThrottle, PollingThrottle

from .models import VisibilityCheck
from .serializers import (
    StartVisibilityCheckSerializer,
    VisibilityCheckDetailSerializer,
    VisibilityCheckListSerializer,
)
from .tasks import start_visibility_task

logger = logging.getLogger("apps")

# Self-heal orphaned checks whose worker died, so a poller doesn't wait forever
# (same failure class as AnalysisRun). Running checks refresh updated_at as they
# advance; PENDING ones may legitimately sit queued, so they get a longer grace.
_STALE_RUNNING = timedelta(minutes=5)
_STALE_PENDING = timedelta(minutes=30)
_TERMINAL_STATUSES = {VisibilityCheck.Status.COMPLETE, VisibilityCheck.Status.FAILED}


def _maybe_fail_stale(check: VisibilityCheck) -> None:
    """Flip a silently-orphaned check to FAILED once it goes quiet past its timeout."""
    if check.status in _TERMINAL_STATUSES:
        return
    is_pending = check.status == VisibilityCheck.Status.PENDING
    timeout = _STALE_PENDING if is_pending else _STALE_RUNNING
    if check.updated_at >= timezone.now() - timeout:
        return
    check.status = VisibilityCheck.Status.FAILED
    if not check.error_message:
        check.error_message = "Check stalled — the worker likely restarted. Please run it again."
    check.save(update_fields=["status", "error_message", "updated_at"])


class StartVisibilityCheckView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request):
        serializer = StartVisibilityCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        check = VisibilityCheck.objects.create(
            brand_name=data["brand_name"],
            brand_url=data["brand_url"],
            email=data.get("email", ""),
            status=VisibilityCheck.Status.PENDING,
        )

        start_visibility_task(check.id)

        return Response(
            {
                "id": check.id,
                "brand_name": check.brand_name,
                "status": check.status,
                "message": "Visibility check started",
            },
            status=status.HTTP_201_CREATED,
        )


class VisibilityCheckListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        checks = VisibilityCheck.objects.filter(email=email)
        serializer = VisibilityCheckListSerializer(checks, many=True)
        return Response(serializer.data)


class VisibilityCheckDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, check_id):
        try:
            check = VisibilityCheck.objects.get(pk=check_id)
        except VisibilityCheck.DoesNotExist:
            return Response(
                {"error": "Visibility check not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = VisibilityCheckDetailSerializer(check)
        return Response(serializer.data)


class VisibilityCheckStatusView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]  # No throttling — polling endpoint

    def get(self, request, check_id):
        try:
            check = VisibilityCheck.objects.get(pk=check_id)
        except VisibilityCheck.DoesNotExist:
            return Response(
                {"error": "Visibility check not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        _maybe_fail_stale(check)

        return Response(
            {
                "id": check.id,
                "status": check.status,
                "progress": check.progress,
                "overall_score": check.overall_score,
            }
        )
