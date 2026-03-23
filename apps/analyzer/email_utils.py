"""Email digest utilities for scheduled analysis reports."""
import logging
import os

from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings

logger = logging.getLogger("apps")

FRONTEND_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")


def send_digest_email(to_email: str, context: dict):
    """Send a GEO score digest email."""
    context["frontend_url"] = FRONTEND_URL
    context["dashboard_url"] = f"{FRONTEND_URL}/dashboard/{context.get('slug', '')}"

    try:
        html_body = render_to_string("analyzer/digest_email.html", context)
    except Exception:
        logger.exception("Failed to render digest email template")
        html_body = _fallback_html(context)

    score = context.get("score", 0)
    brand = context.get("brand_name", "Your site")

    try:
        send_mail(
            subject=f"Signalor GEO Report: {brand} scored {score}/100",
            message=_plain_text(context),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to_email],
            html_message=html_body,
            fail_silently=False,
        )
    except Exception:
        logger.exception(f"Failed to send digest email to {to_email}")


def _plain_text(ctx: dict) -> str:
    lines = [
        f"GEO Score Report for {ctx.get('brand_name', 'your site')}",
        f"URL: {ctx.get('url', '')}",
        f"Current Score: {ctx.get('score', 0)}/100",
    ]
    if ctx.get("score_change") is not None:
        sign = "+" if ctx["score_change"] >= 0 else ""
        lines.append(f"Change: {sign}{ctx['score_change']} points")
    if ctx.get("recommendations"):
        lines.append("\nTop Recommendations:")
        for rec in ctx["recommendations"]:
            lines.append(f"  - [{rec['priority']}] {rec['title']}")
    lines.append(f"\nView full report: {ctx.get('dashboard_url', '')}")
    return "\n".join(lines)


def _fallback_html(ctx: dict) -> str:
    return f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <h2>GEO Score Report</h2>
        <p><strong>{ctx.get('brand_name', 'Your site')}</strong></p>
        <p style="font-size:48px;font-weight:bold;color:#2563eb;">{ctx.get('score', 0)}/100</p>
        <p><a href="{ctx.get('dashboard_url', '#')}">View Full Report</a></p>
    </div>
    """
