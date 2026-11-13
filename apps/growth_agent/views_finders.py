"""The brand owner connects their own Hunter or Apollo account.

GET  programs/<slug>/finders/                      -> what is connected
POST programs/<slug>/finders/<provider>/sign-in/    {"return_url"} -> provider login URL
GET  oauth/finders/callback/                        <- the provider redirects back here
POST programs/<slug>/finders/<provider>/connect/    {"api_key": "..."} (fallback)
POST programs/<slug>/finders/<provider>/disconnect/

Owner-only (the program lookup is scoped to the signed-in owner). Tokens and
keys are stored encrypted and never returned or logged; responses and the
Agent log carry at most a key's last four characters.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponseRedirect
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from .models import AgentActionLog
from .services.execution import audit
from .services.outreach.contact_finders import accounts, oauth
from .services.outreach.contact_finders.base import FinderError
from .views import GrowthBaseView
from .views_control import _program_or_404

logger = logging.getLogger("apps")
UNKNOWN_PROVIDER = {"detail": "Unknown contact finder."}


class ApiKeySerializer(serializers.Serializer):
    api_key = serializers.CharField(max_length=accounts.MAX_KEY_LENGTH, trim_whitespace=True)


def _listing(program) -> Response:
    return Response({"finders": accounts.describe(program.organization)})


class FinderListView(GrowthBaseView):
    def get(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        return missing or _listing(program)


class FinderConnectView(GrowthBaseView):
    def post(self, request, org_slug: str, provider: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        if provider not in accounts.PROVIDERS:
            return Response(UNKNOWN_PROVIDER, status=status.HTTP_404_NOT_FOUND)
        payload = ApiKeySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        api_key = payload.validated_data["api_key"]
        try:
            accounts.connect(program.organization, provider, api_key)
        except FinderError as exc:
            return Response({"detail": str(exc), "code": "key_rejected"}, status=status.HTTP_400_BAD_REQUEST)
        detail = {"provider": provider, "key_hint": api_key[-accounts.HINT_LENGTH :]}
        audit.record(
            program,
            None,
            "finder_connected",
            AgentActionLog.Outcome.SUCCEEDED,
            detail,
            actor_email=request.user.email,
        )
        return _listing(program)


class FinderDisconnectView(GrowthBaseView):
    def post(self, request, org_slug: str, provider: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        if provider not in accounts.PROVIDERS:
            return Response(UNKNOWN_PROVIDER, status=status.HTTP_404_NOT_FOUND)
        if accounts.disconnect(program.organization, provider):
            audit.record(
                program,
                None,
                "finder_disconnected",
                AgentActionLog.Outcome.SUCCEEDED,
                {"provider": provider},
                actor_email=request.user.email,
            )
        return _listing(program)


class SignInSerializer(serializers.Serializer):
    # The Agent mode page to come back to; checked against GROWTH_APP_ORIGINS.
    return_url = serializers.URLField(max_length=500)


class FinderSignInView(GrowthBaseView):
    """POST programs/<slug>/finders/<provider>/sign-in/ {"return_url"} -> {"authorize_url"}.

    Registers Signalor with the provider's sign-in server and returns the
    provider's own login page for the browser to open."""

    def post(self, request, org_slug: str, provider: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        if provider not in oauth.SERVERS:
            return Response(UNKNOWN_PROVIDER, status=status.HTTP_404_NOT_FOUND)
        payload = SignInSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return_url = payload.validated_data["return_url"]
        if not oauth.valid_return_url(return_url):
            return Response({"detail": "Unexpected return page."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            url = oauth.start(program, provider, request.user.email, return_url)
        except FinderError:
            logger.warning("finder sign-in could not start for %s", provider, exc_info=True)
            detail = (
                f"{accounts.LABELS[provider]} sign-in is unavailable right now. Try again, or use an API key."
            )
            return Response(
                {"detail": detail, "code": "sign_in_unavailable"}, status=status.HTTP_502_BAD_GATEWAY
            )
        return Response({"authorize_url": url})


def _back_to_app(return_url: str, **params: str) -> HttpResponseRedirect:
    base = return_url if oauth.valid_return_url(return_url) else settings.FRONTEND_BASE_URL
    return HttpResponseRedirect(f"{base}?{urlencode(params)}")


class FinderOAuthCallbackView(APIView):
    """Hunter or Apollo redirects the browser here after login. Trust comes from
    the encrypted, expiring state (not the service signature), and the brand's
    ownership is re-checked before any token is stored."""

    authentication_classes = []
    permission_classes = []
    throttle_classes = [AnonRateThrottle]

    def get(self, request):
        state = request.query_params.get("state", "")
        try:
            if request.query_params.get("error"):
                data = oauth.read_state(state)
                return _back_to_app(data["return_url"], finder_error="denied", finder=data["provider"])
            program, data = oauth.finish(request.query_params.get("code", ""), state)
        except oauth.ConnectError as exc:
            return _back_to_app(exc.return_url, finder_error=exc.code)
        detail = {"provider": data["provider"], "method": accounts.METHOD_OAUTH}
        audit.record(
            program,
            None,
            "finder_connected",
            AgentActionLog.Outcome.SUCCEEDED,
            detail,
            actor_email=data["email"],
        )
        return _back_to_app(data["return_url"], finder_connected=data["provider"])
