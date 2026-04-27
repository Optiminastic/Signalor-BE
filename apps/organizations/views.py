import logging

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Organization
from .serializers import OnboardSerializer, OrganizationSerializer

logger = logging.getLogger("apps")


class OnboardView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = OnboardSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]

        # Enforce project limit
        from apps.accounts.subscription_utils import plan_limit_error_response_dict, project_limit_reached

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

        orgs = Organization.objects.filter(owner_email=email)
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
