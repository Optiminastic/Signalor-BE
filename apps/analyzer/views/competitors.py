"""Competitor CRUD for a run."""

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions.throttling import (
    ExpensiveThrottle,
)

from ..models import (
    AnalysisRun,
    Competitor,
)


class CompetitorListCreateView(APIView):
    """GET/POST /api/analyzer/runs/s/<slug>/competitors/"""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        competitors = run.competitors.all().order_by("-scored", "-composite_score")
        from ..serializers import CompetitorSerializer

        return Response(CompetitorSerializer(competitors, many=True).data)

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        name = request.data.get("name", "").strip()
        url = request.data.get("url", "").strip()
        if not name or not url:
            return Response({"error": "name and url are required."}, status=status.HTTP_400_BAD_REQUEST)
        competitor = run.competitors.create(name=name, url=url)
        from ..serializers import CompetitorSerializer

        return Response(CompetitorSerializer(competitor).data, status=status.HTTP_201_CREATED)

class CompetitorDetailView(APIView):
    """PATCH/DELETE /api/analyzer/runs/s/<slug>/competitors/<id>/"""

    permission_classes = [AllowAny]

    def patch(self, request, slug, competitor_id):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        competitor = get_object_or_404(Competitor, pk=competitor_id, analysis_run=run)
        if "name" in request.data:
            competitor.name = request.data["name"].strip()
        if "url" in request.data:
            competitor.url = request.data["url"].strip()
        competitor.save(update_fields=["name", "url"])
        from ..serializers import CompetitorSerializer

        return Response(CompetitorSerializer(competitor).data)

    def delete(self, request, slug, competitor_id):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        competitor = get_object_or_404(Competitor, pk=competitor_id, analysis_run=run)
        competitor.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
