"""
Email the weekly report to the brand owner: the same text as the report behind
the Agent page's icon, as plain text and simple HTML, with a link back to it.

Sent through Django's mail backend (Resend in production). A failure is logged
and never stops the report from being saved; ``emailed_at`` stays empty.
"""

from __future__ import annotations

import logging
import re
from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from ..models import WeeklyReport

logger = logging.getLogger("apps")

BOLD = re.compile(r"\*\*([^*]+)\*\*")
HEADING = re.compile(r"^#{2,3}\s+")
BULLET = re.compile(r"^(\s*)[-*]\s+")
ACCENT = "#e04a3d"


def report_url(report: WeeklyReport) -> str:
    slug = report.program.organization.slug
    return f"{settings.FRONTEND_BASE_URL}/dashboard/{slug}/agent-mode?report={report.week_of.isoformat()}"


def plain_text(content: str) -> str:
    """The report without formatting symbols."""
    lines = []
    for line in content.splitlines():
        line = BOLD.sub(r"\1", line)
        if HEADING.match(line):
            line = HEADING.sub("", line).upper()
        lines.append(BULLET.sub(r"\1- ", line))
    return "\n".join(lines)


def _inline(text: str) -> str:
    return BOLD.sub(r"<strong>\1</strong>", escape(text))


def html(content: str, url: str) -> str:
    """Escaped HTML: headings, bold and bullets only, so report text can't inject markup."""
    out, in_list = [], False
    for raw in content.splitlines():
        bullet = BULLET.match(raw)
        if bullet and not in_list:
            out.append('<ul style="margin:4px 0 12px;padding-left:20px">')
            in_list = True
        if not bullet and in_list:
            out.append("</ul>")
            in_list = False
        if HEADING.match(raw):
            out.append(f'<h3 style="margin:18px 0 6px;font-size:15px">{_inline(HEADING.sub("", raw))}</h3>')
        elif bullet:
            indent = "margin-left:16px;" if bullet.group(1) else ""
            out.append(f'<li style="{indent}margin:2px 0">{_inline(BULLET.sub("", raw))}</li>')
        elif raw.strip():
            out.append(f'<p style="margin:0 0 10px">{_inline(raw)}</p>')
    if in_list:
        out.append("</ul>")
    body = "\n".join(out)
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:14px;'
        'line-height:1.5;color:#1f1f1f;max-width:560px">'
        f"{body}"
        f'<p style="margin:22px 0 0"><a href="{escape(url)}" style="background:{ACCENT};color:#fff;'
        'padding:9px 16px;border-radius:6px;text-decoration:none;font-weight:600">'
        "View the report in signalor.ai</a></p></div>"
    )


def send(report: WeeklyReport) -> bool:
    """Email the report to the brand owner. True if sent; records ``emailed_at``."""
    org = report.program.organization
    if not org.owner_email:
        return False
    url = report_url(report)
    message = EmailMultiAlternatives(
        subject=f"{org.name}: your weekly report ({report.week_of.strftime('%d %b')})",
        body=f"{plain_text(report.content)}\n\nView it in signalor.ai: {url}\n",
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[org.owner_email],
    )
    message.attach_alternative(html(report.content, url), "text/html")
    try:
        message.send()
    except Exception:  # noqa: BLE001 - a mail failure must never lose the report
        logger.exception("growth weekly report email failed for report %s", report.pk)
        return False
    WeeklyReport.objects.filter(pk=report.pk).update(emailed_at=timezone.now())
    return True
