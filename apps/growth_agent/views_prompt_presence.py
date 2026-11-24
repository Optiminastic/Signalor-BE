"""Prompt presence: where the brand's tracked prompts are discussed.

GET  programs/<slug>/prompt-presence/        -> per prompt: Google, Reddit, LinkedIn, X results
POST programs/<slug>/prompt-presence/check/  -> search now (at most once an hour)

Owner-only (the program lookup is scoped to the signed-in owner).
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from .services.prompt_presence import check
from .views import GrowthBaseView
from .views_control import _program_or_404


class PromptPresenceView(GrowthBaseView):
    def get(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        return missing or Response(check.report(program))


class PromptPresenceCheckView(GrowthBaseView):
    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        if not check.start(program, guard=check.START_GUARD, actor_email=request.user.email):
            detail = "Prompts were checked in the last hour. Check again later."
            return Response({"detail": detail, "code": "check_not_started"}, status=status.HTTP_409_CONFLICT)
        return Response(check.report(program), status=status.HTTP_202_ACCEPTED)
