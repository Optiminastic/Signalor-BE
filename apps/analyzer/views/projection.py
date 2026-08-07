"""30-day AI-visibility projection for the dashboard overview."""

from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions.throttling import PollingThrottle

from ..models import AnalysisRun
from ..serializers import ProjectionSerializer
from ..services.projection import build_projection


class ProjectionView(APIView):
    """GET /runs/s/<slug>/projection/

    A conservative 30-day forward projection for the brand behind ``slug``:
    where its AI-search visibility and recommendation rate can plausibly get to,
    how many competitors that would overtake, and how many prompts are still
    weak enough to be worth strengthening.

    Slug-scoped and ``AllowAny`` like every other run read on the overview - the
    unguessable run slug is the capability - and rate-limited as a dashboard poll.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        run = get_object_or_404(
            AnalysisRun.objects.select_related("brand_visibility").prefetch_related("competitors"),
            slug=slug,
        )
        payload = build_projection(run)
        return Response(ProjectionSerializer(payload).data)
