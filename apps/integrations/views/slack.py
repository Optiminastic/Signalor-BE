"""Slack connect flow: authorize -> callback -> pick a channel.

Thin HTTP layer. Every Slack call goes through services/slack/client.py; this
module only parses requests, verifies state, and persists the Integration row.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions.throttling import ExpensiveThrottle, PollingThrottle

from ..models import Integration
from ..services.slack import client as slack_client
from ._shared import (
    _org_id_param,
    _redirect_with_status,
    _resolve_org,
    _sign_state,
    _verify_state,
    logger,
)

# Minimal for phase 1: post messages, and read the channel list for the picker.
# chat:write.public lets the bot post without being invited to the channel first.
SLACK_SCOPES = "chat:write,chat:write.public,channels:read"

_AUTHORIZE = "https://slack.com/oauth/v2/authorize"


def _redirect_uri() -> str:
    base = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base}/api/integrations/slack/callback/"


class SlackAuthURLView(APIView):
    """GET slack/auth-url/?email=&org_id=&return_to= -> where to send the user."""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request):
        if not slack_client.is_configured():
            return Response(
                {"error": "Slack is not configured on this server.", "code": "slack_not_configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        org, err = _resolve_org(email, _org_id_param(request))
        if err:
            return err

        # Signed so the callback can trust which org it is completing for.
        state = _sign_state(
            {
                "org_id": org.id,
                "email": email,
                "return_to": request.query_params.get("return_to", "").strip() or "/settings/integrations",
            }
        )
        params = {
            "client_id": os.getenv("SLACK_CLIENT_ID", ""),
            "scope": SLACK_SCOPES,
            "redirect_uri": _redirect_uri(),
            "state": state,
        }
        return Response({"auth_url": f"{_AUTHORIZE}?{urlencode(params)}"})


class SlackCallbackView(APIView):
    """GET slack/callback/ — Slack sends the user back here with a code."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request):
        payload = _verify_state(request.query_params.get("state", ""))
        return_to = (payload or {}).get("return_to", "/settings/integrations")
        if payload is None:
            # Unsigned or tampered state: refuse rather than trust the org id.
            return _redirect_with_status(False, "bad_state", return_to, provider="slack")

        code = request.query_params.get("code", "").strip()
        if not code:
            reason = request.query_params.get("error", "no_code")
            return _redirect_with_status(False, reason, return_to, provider="slack")

        try:
            data = slack_client.exchange_code(code, _redirect_uri())
        except Exception:
            logger.exception("slack: OAuth exchange failed for org=%s", payload.get("org_id"))
            return _redirect_with_status(False, "exchange_failed", return_to, provider="slack")

        token = (data.get("access_token") or "").strip()
        team = data.get("team") or {}
        if not token:
            return _redirect_with_status(False, "no_token", return_to, provider="slack")

        integration, _ = Integration.objects.update_or_create(
            organization_id=payload["org_id"],
            provider=Integration.Provider.SLACK,
            defaults={
                "is_active": True,
                # Channel is chosen in the next step; until then nothing is sent.
                "metadata": {
                    "team_id": team.get("id", ""),
                    "team_name": team.get("name", ""),
                    "bot_user_id": data.get("bot_user_id", ""),
                    "channel_id": "",
                    "channel_name": "",
                },
            },
        )
        integration.set_access_token(token)
        integration.save(update_fields=["access_token_encrypted", "updated_at"])
        return _redirect_with_status(True, "", return_to, provider="slack")


class SlackChannelsView(APIView):
    """GET slack/channels/?email= -> channels the bot can post to."""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request):
        org, err = _resolve_org(
            request.query_params.get("email", "").lower().strip(), _org_id_param(request)
        )
        if err:
            return err
        integration = Integration.objects.filter(
            organization=org, provider=Integration.Provider.SLACK, is_active=True
        ).first()
        if integration is None:
            return Response({"channels": []})
        try:
            return Response({"channels": slack_client.list_channels(integration.get_access_token())})
        except Exception:
            logger.exception("slack: channel list failed for org=%s", org.id)
            return Response(
                {"error": "Could not read the channel list.", "code": "slack_list_failed"},
                status=status.HTTP_502_BAD_GATEWAY,
            )


class SlackSelectChannelView(APIView):
    """POST slack/select-channel/ {channel_id, channel_name} — where reports go."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request):
        org, err = _resolve_org(
            (request.data.get("email") or "").lower().strip(), _org_id_param(request)
        )
        if err:
            return err
        channel_id = (request.data.get("channel_id") or "").strip()
        if not channel_id:
            return Response({"error": "channel_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        integration = Integration.objects.filter(
            organization=org, provider=Integration.Provider.SLACK, is_active=True
        ).first()
        if integration is None:
            return Response({"error": "Slack is not connected."}, status=status.HTTP_404_NOT_FOUND)

        meta = dict(integration.metadata or {})
        meta["channel_id"] = channel_id
        meta["channel_name"] = (request.data.get("channel_name") or "").strip()
        integration.metadata = meta
        integration.save(update_fields=["metadata", "updated_at"])
        return Response({"status": "ok", "channel_id": channel_id})


class SlackDisconnectView(APIView):
    """POST slack/disconnect/ — stop sending reports to this workspace."""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request):
        org, err = _resolve_org(
            (request.data.get("email") or "").lower().strip(), _org_id_param(request)
        )
        if err:
            return err
        n = Integration.objects.filter(
            organization=org, provider=Integration.Provider.SLACK, is_active=True
        ).update(is_active=False)
        return Response({"status": "disconnected", "count": n})
