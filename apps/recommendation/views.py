from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from .services.pdf_parser import extract_text_from_bytes, extract_text_from_path
from .services.recommendation_engine import generate_recommendations
from .services.pdf_renderer import render_recommendations_pdf


class RecommendationFromDiscoveryReportView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, JSONParser]

    def post(self, request):
        file_obj = request.FILES.get("report_pdf")
        pdf_path = request.data.get("pdf_path") or ""
        report_filename = request.data.get("report_filename") or ""
        source_url = request.data.get("source_url") or "Unknown"
        output_format = request.query_params.get("format", "pdf").lower()

        if not file_obj and not pdf_path and not report_filename:
            return Response(
                {"error": "Provide report_pdf upload, pdf_path, or report_filename."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            if file_obj:
                text = extract_text_from_bytes(file_obj.read())
            elif report_filename:
                reports_dir = getattr(settings, "DISCOVERY_REPORTS_DIR", None)
                if not reports_dir:
                    return Response(
                        {"error": "DISCOVERY_REPORTS_DIR is not configured."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                path = Path(reports_dir) / report_filename
                if not path.exists():
                    return Response(
                        {"error": "Report file not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )
                text = extract_text_from_path(str(path))
            else:
                if not os.path.exists(pdf_path):
                    return Response(
                        {"error": "Provided pdf_path does not exist."},
                        status=status.HTTP_404_NOT_FOUND,
                    )
                text = extract_text_from_path(pdf_path)

            if not text:
                return Response(
                    {"error": "No text could be extracted from the report PDF."},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

            recs = generate_recommendations(text)

            if output_format == "json":
                return Response(
                    {
                        "source_url": source_url,
                        "recommendations": [
                            {
                                "title": r.title,
                                "priority": r.priority,
                                "category": r.category,
                                "reason": r.reason,
                                "action": r.action,
                                "evidence": r.evidence,
                            }
                            for r in recs
                        ],
                    }
                )

            pdf_bytes = render_recommendations_pdf(
                source_url=source_url,
                recommendations=recs,
            )
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = 'attachment; filename="recommendations.pdf"'
            return response

        except RuntimeError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception as exc:
            return Response(
                {"error": f"Failed to generate recommendations: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
