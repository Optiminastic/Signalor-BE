from __future__ import annotations

from io import BytesIO
from typing import Iterable

from django.template.loader import render_to_string

from .recommendation_engine import Recommendation, summarize_recommendations


def render_recommendations_pdf(
    *,
    source_url: str,
    recommendations: Iterable[Recommendation],
) -> bytes:
    try:
        from xhtml2pdf import pisa
    except Exception as exc:  # pragma: no cover - dependency error
        raise RuntimeError("PDF rendering library is not available.") from exc

    rec_list = list(recommendations)
    summary = summarize_recommendations(rec_list)

    html = render_to_string(
        "recommendation/report.html",
        {
            "source_url": source_url,
            "recommendations": rec_list,
            "summary": summary,
        },
    )

    buffer = BytesIO()
    pisa_status = pisa.CreatePDF(html, dest=buffer, encoding="utf-8")
    if pisa_status.err:
        raise RuntimeError("PDF generation failed.")
    buffer.seek(0)
    return buffer.read()
