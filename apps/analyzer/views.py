import logging

from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.models import Organization

from .models import AnalysisRun
from .serializers import (
    AnalysisRunDetailSerializer,
    AnalysisRunListSerializer,
    StartAnalysisSerializer,
)
from .tasks import start_analysis_task

logger = logging.getLogger("apps")


class StartAnalysisView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = StartAnalysisSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        email = data.get("email", "")

        # Try to link to organization
        org = None
        if email:
            org = Organization.objects.filter(owner_email=email).first()

        run = AnalysisRun.objects.create(
            organization=org,
            url=data["url"],
            brand_name=data.get("brand_name", ""),
            email=email,
            run_type=data["run_type"],
            status=AnalysisRun.Status.PENDING,
        )

        # Start background task
        start_analysis_task(run.id)

        return Response(
            {
                "id": run.id,
                "url": run.url,
                "status": run.status,
                "message": "Analysis started",
            },
            status=status.HTTP_201_CREATED,
        )


class AnalysisRunListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        runs = AnalysisRun.objects.filter(email=email)
        serializer = AnalysisRunListSerializer(runs, many=True)
        return Response(serializer.data)


class AnalysisRunDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, run_id):
        try:
            run = AnalysisRun.objects.get(pk=run_id)
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Analysis run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AnalysisRunDetailSerializer(run)
        return Response(serializer.data)


class AnalysisRunStatusView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = []  # No throttling — this is a polling endpoint

    def get(self, request, run_id):
        try:
            run = AnalysisRun.objects.get(pk=run_id)
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Analysis run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "id": run.id,
                "status": run.status,
                "progress": run.progress,
                "composite_score": run.composite_score,
            }
        )


class ExportPDFView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, run_id):
        try:
            run = AnalysisRun.objects.get(pk=run_id)
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Analysis run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if run.status != AnalysisRun.Status.COMPLETE:
            return Response(
                {"error": "Analysis must be complete before exporting."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from django.template.loader import render_to_string
            from xhtml2pdf import pisa
            from io import BytesIO

            main_page = run.page_scores.filter(url=run.url).first()
            recommendations = run.recommendations.all()
            competitors = run.competitors.filter(scored=True)

            context = {
                "run": run,
                "main_page": main_page,
                "recommendations": recommendations,
                "competitors": competitors,
                "ai_probes": run.ai_probes.all(),
            }

            html_string = render_to_string("analyzer/report.html", context)
            result = BytesIO()
            pdf = pisa.CreatePDF(html_string, dest=result)

            if pdf.err:
                return Response(
                    {"error": "PDF generation failed."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            response = HttpResponse(result.getvalue(), content_type="application/pdf")
            response["Content-Disposition"] = (
                f'attachment; filename="geo-analysis-{run.id}.pdf"'
            )
            return response

        except ImportError:
            return Response(
                {"error": "PDF export requires xhtml2pdf package."},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
