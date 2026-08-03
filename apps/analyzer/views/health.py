"""Liveness and worker-health probes."""

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions.throttling import (
    PollingThrottle,
)


class HealthCheckView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request):
        from django.db import connection

        health_status = {
            "status": "healthy",
            "service": "geo-be",
            "timestamp": timezone.now().isoformat(),
        }
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            health_status["database"] = "connected"
        except Exception as e:
            health_status["status"] = "unhealthy"
            health_status["database"] = f"error: {str(e)}"
            return Response(health_status, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            from django.core.cache import cache

            cache.set("health_check", "ok", 10)
            cache_value = cache.get("health_check")
            health_status["cache"] = "connected" if cache_value == "ok" else "degraded"
        except Exception as e:
            health_status["cache"] = f"error: {str(e)}"

        return Response(health_status, status=status.HTTP_200_OK)

class WorkerHealthView(APIView):
    """Readiness of the analysis worker — 503 when nothing is consuming.

    Separate from ``/health/`` (web liveness) because a live web process with a
    dead analysis worker is a distinct failure: the API answers fine but no run
    can finish. Point an uptime monitor here to get alerted when the worker dies
    or its RabbitMQ broker is unreachable.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request):
        from ..health import analysis_worker_health

        worker = analysis_worker_health()
        payload = {
            "status": "healthy" if worker["ok"] else "unhealthy",
            "service": "geo-be-analysis-worker",
            "timestamp": timezone.now().isoformat(),
            "worker": worker,
        }
        code = status.HTTP_200_OK if worker["ok"] else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(payload, status=code)
