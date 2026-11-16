"""Guest posting: sites that accept guest articles on the brand's topic.

GET  programs/<slug>/guest-posts/        -> this month's guest-post sites and where each stands
POST programs/<slug>/guest-posts/find/   -> search for more sites now (at most every 6 hours)

Owner-only (the program lookup is scoped to the signed-in owner). Skipping or
restoring a site uses the existing task endpoint.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from .services.guest_posts import finder
from .services.guest_posts import report as guest_report
from .views import GrowthBaseView
from .views_control import _program_or_404


class GuestPostsView(GrowthBaseView):
    def get(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        return missing or Response(guest_report.report(program))


class GuestPostsFindView(GrowthBaseView):
    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        if not finder.start(program, guard=finder.START_GUARD, actor_email=request.user.email):
            detail = "A search for guest-post sites ran recently. It runs at most every 6 hours."
            return Response({"detail": detail, "code": "find_not_started"}, status=status.HTTP_409_CONFLICT)
        return Response(guest_report.report(program), status=status.HTTP_202_ACCEPTED)
