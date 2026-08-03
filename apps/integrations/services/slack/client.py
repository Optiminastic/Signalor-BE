"""Slack Web API client.

Only speaks HTTP to Slack. No ORM, no formatting, no knowledge of what a run or
a task is — that separation is what lets `notify` be swapped or tested without
a workspace.

Config (env):
    SLACK_CLIENT_ID / SLACK_CLIENT_SECRET  - OAuth app credentials
    SLACK_SIGNING_SECRET                   - verifies inbound requests
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time

from apps.integrations._http import request_with_retry

logger = logging.getLogger("apps")

_API = "https://slack.com/api"

# Slack rejects request signatures older than five minutes; matching that here
# is the replay-protection window.
SIGNATURE_MAX_AGE_SEC = 60 * 5


class SlackError(RuntimeError):
    """Slack returned ok=false. The message carries Slack's own error code."""


class SlackNotConfigured(RuntimeError):
    """The app's OAuth credentials are absent, so the flow cannot run here."""


def _credentials() -> tuple[str, str]:
    client_id = os.getenv("SLACK_CLIENT_ID", "").strip()
    client_secret = os.getenv("SLACK_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SlackNotConfigured("SLACK_CLIENT_ID / SLACK_CLIENT_SECRET are not set.")
    return client_id, client_secret


def is_configured() -> bool:
    """True when this deployment can run the Slack OAuth flow at all."""
    try:
        _credentials()
    except SlackNotConfigured:
        return False
    return True


def _call(method: str, token: str, payload: dict) -> dict:
    resp = request_with_retry(
        "POST",
        f"{_API}/{method}",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        # Slack puts the reason in `error`; never log the token.
        raise SlackError(data.get("error") or "unknown_error")
    return data


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Trade the OAuth callback code for a bot token.

    Returns the raw Slack payload; the caller decides what to persist.
    """
    client_id, client_secret = _credentials()
    resp = request_with_retry(
        "POST",
        f"{_API}/oauth.v2.access",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise SlackError(data.get("error") or "oauth_failed")
    return data


def post_message(token: str, channel: str, blocks: list[dict], fallback: str) -> dict:
    """Post to a channel. ``fallback`` is the notification/preview text."""
    return _call("chat.postMessage", token, {"channel": channel, "blocks": blocks, "text": fallback})


def list_channels(token: str, limit: int = 200) -> list[dict]:
    """Public channels the bot can post to, for the channel picker."""
    resp = request_with_retry(
        "GET",
        f"{_API}/conversations.list",
        params={"types": "public_channel", "limit": limit, "exclude_archived": "true"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise SlackError(data.get("error") or "list_failed")
    return [{"id": c["id"], "name": c["name"]} for c in data.get("channels", [])]


def verify_signature(*, timestamp: str, signature: str, body: bytes) -> bool:
    """Verify an inbound Slack request (CLAUDE.md 5.4).

    Rejects anything older than five minutes so a captured request cannot be
    replayed, and compares in constant time.
    """
    secret = os.getenv("SLACK_SIGNING_SECRET", "").strip()
    if not secret or not timestamp or not signature:
        return False
    try:
        age = abs(time.time() - int(timestamp))
    except (TypeError, ValueError):
        return False
    if age > SIGNATURE_MAX_AGE_SEC:
        return False
    basestring = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
