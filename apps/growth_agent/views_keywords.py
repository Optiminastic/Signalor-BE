"""Keyword data for the brand (DataForSEO, signalor.ai's own account).

GET  programs/<slug>/keywords/          -> the brand's keywords and its gaps against the top 3 competitors
POST programs/<slug>/keywords/refresh/  -> read them again now (at most once a day; each read is paid)

Owner-only: the program lookup is scoped to the signed-in owner.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from .services.keywords import refresh
from .services.keywords import report as keyword_report
from .views import GrowthBaseView
from .views_control import _program_or_404


class KeywordsView(GrowthBaseView):
    def get(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        return missing or Response(keyword_report.report(program))


class KeywordsRefreshView(GrowthBaseView):
    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        if not refresh.start(program, guard=refresh.START_GUARD, actor_email=request.user.email):
            detail = "Keywords were read recently. They refresh at most once a day."
            return Response(
                {"detail": detail, "code": "refresh_not_started"}, status=status.HTTP_409_CONFLICT
            )
        return Response(keyword_report.report(program), status=status.HTTP_202_ACCEPTED)
