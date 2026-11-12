"""The brand owner connects their own Hunter or Apollo account.

GET  programs/<slug>/finders/                      -> what is connected
POST programs/<slug>/finders/<provider>/connect/    {"api_key": "..."}
POST programs/<slug>/finders/<provider>/disconnect/

Owner-only (the program lookup is scoped to the signed-in owner). The key is
checked with the provider, stored encrypted, and never returned or logged:
responses and the Agent log carry only its last four characters.
"""

from __future__ import annotations

from rest_framework import serializers, status
from rest_framework.response import Response

from .models import AgentActionLog
from .services.execution import audit
from .services.outreach.contact_finders import accounts
from .services.outreach.contact_finders.base import FinderError
from .views import GrowthBaseView
from .views_control import _program_or_404

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
