import logging

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import VisibilityCheck
from .serializers import (
    StartVisibilityCheckSerializer,
    VisibilityCheckDetailSerializer,
    VisibilityCheckListSerializer,
)
from .tasks import start_visibility_task

logger = logging.getLogger("apps")


class StartVisibilityCheckView(APIView):
    permission_classes = [AllowAny]

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
    throttle_classes = []  # No throttling — polling endpoint

    def get(self, request, check_id):
        try:
            check = VisibilityCheck.objects.get(pk=check_id)
        except VisibilityCheck.DoesNotExist:
            return Response(
                {"error": "Visibility check not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "id": check.id,
                "status": check.status,
                "progress": check.progress,
                "overall_score": check.overall_score,
            }
        )
