"""Weekly reports behind the Agent page's report icon.

GET programs/<slug>/weekly-reports/ -> {"reports": [...latest first], "current": {...}, "next_report_on": date}

``reports`` are the saved weekly reports (also emailed to the owner). ``current``
is this week so far, built live from the same recorded facts, so the icon always
has something to show, even before the first weekly report. Owner-only (the
program lookup is scoped to the signed-in owner).
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework.response import Response

from .services import weekly_summary
from .views import GrowthBaseView
from .views_control import _program_or_404

MAX_REPORTS = 12


def _present(report) -> dict:
    return {
        "week_of": report.week_of.isoformat(),
        "content": report.content,
        "created_at": report.created_at.isoformat(),
        "emailed": report.emailed_at is not None,
    }


class WeeklyReportListView(GrowthBaseView):
    def get(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        reports = program.weekly_reports.all()[:MAX_REPORTS]
        today = timezone.now().date()
        return Response(
            {
                "reports": [_present(r) for r in reports],
                "current": {
                    "week_of": weekly_summary.week_of(today).isoformat(),
                    "content": weekly_summary.build(program),
                },
                "next_report_on": weekly_summary.next_report_on(program).isoformat(),
            }
        )
