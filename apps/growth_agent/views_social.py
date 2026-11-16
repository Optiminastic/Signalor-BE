"""The brand's social presence score.

GET  programs/<slug>/social/                    -> latest score, plus whether Buffer is connected
POST programs/<slug>/social/check/              -> measure now (at most every 6 hours)
POST programs/<slug>/social/buffer/sign-in/     {"return_url"} -> Buffer's login page
POST programs/<slug>/social/buffer/disconnect/  -> forget the Buffer sign-in

Owner-only (the program lookup is scoped to the signed-in owner).
"""

from __future__ import annotations

import logging

from rest_framework import serializers, status
from rest_framework.response import Response

from .models import AgentActionLog
from .services.execution import audit
from .services.outreach.contact_finders import accounts, oauth
from .services.outreach.contact_finders.base import FinderError
from .services.social import buffer as social_buffer
from .services.social import check as social_check
from .views import GrowthBaseView
from .views_control import _program_or_404

logger = logging.getLogger("apps")
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


class SignInSerializer(serializers.Serializer):
    return_url = serializers.URLField(max_length=500)


class BufferSignInView(GrowthBaseView):
    """Registers Signalor with Buffer's sign-in server and returns Buffer's own login page."""

    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        payload = SignInSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return_url = payload.validated_data["return_url"]
        if not oauth.valid_return_url(return_url):
            return Response({"detail": "Unexpected return page."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            url = oauth.start(program, oauth.BUFFER, request.user.email, return_url)
        except FinderError:
            logger.warning("buffer sign-in could not start", exc_info=True)
            detail = "Buffer sign-in is unavailable right now. Try again in a minute."
            return Response(
                {"detail": detail, "code": "sign_in_unavailable"}, status=status.HTTP_502_BAD_GATEWAY
            )
        return Response({"authorize_url": url})


class BufferDisconnectView(GrowthBaseView):
    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        if accounts.disconnect(program.organization, social_buffer.PROVIDER):
            audit.record(
                program,
                None,
                "buffer_disconnected",
                AgentActionLog.Outcome.SUCCEEDED,
                {},
                actor_email=request.user.email,
            )
        return Response(social_check.report(program))
