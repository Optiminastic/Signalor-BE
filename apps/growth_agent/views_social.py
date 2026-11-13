"""The brand's social presence score.

GET  programs/<slug>/social/        -> latest score per platform and overall, change since last week
POST programs/<slug>/social/check/  -> measure now (at most every 6 hours)

Owner-only (the program lookup is scoped to the signed-in owner).
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from .services.social import check as social_check
from .views import GrowthBaseView
from .views_control import _program_or_404

NOW_GUARD = social_check.START_GUARD


class SocialView(GrowthBaseView):
    def get(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        return missing or Response(social_check.report(program))


class SocialCheckView(GrowthBaseView):
    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        if not social_check.start(program, guard=NOW_GUARD, actor_email=request.user.email):
            detail = "A social check ran recently. It runs at most every 6 hours."
            return Response({"detail": detail, "code": "check_not_started"}, status=status.HTTP_409_CONFLICT)
        return Response(social_check.report(program), status=status.HTTP_202_ACCEPTED)
