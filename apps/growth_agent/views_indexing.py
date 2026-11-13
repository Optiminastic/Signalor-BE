"""The brand's indexing report, and "check now".

GET  programs/<slug>/indexing/        -> latest Search Console check vs the one before
POST programs/<slug>/indexing/check/  -> queue a check now (at most one per 6 hours)

Owner-only (the program lookup is scoped to the signed-in owner).
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from .services.indexing import report, weekly
from .views import GrowthBaseView
from .views_control import _program_or_404


class IndexingView(GrowthBaseView):
    def get(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        return missing or Response(report.build(program.organization))


class IndexingCheckView(GrowthBaseView):
    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        if not weekly.start_check(program, actor_email=request.user.email):
            built = report.build(program.organization)
            if built["status"] in ("not_connected", "no_property"):
                detail = "Connect Search Console and pick the site's property first."
            else:
                detail = (
                    "A check ran recently. Each check reads every page, so it runs at most every 6 hours."
                )
            return Response({"detail": detail, "code": "check_not_started"}, status=status.HTTP_409_CONFLICT)
        return Response(report.build(program.organization), status=status.HTTP_202_ACCEPTED)
