"""
Gmail adapter for outreach: OAuth, send (new or in-thread), and read replies.

Tokens live in Signalor's ``Integration`` model (provider ``gmail``, Fernet-
encrypted) and are refreshed with the same Google credential helpers the GA4
and Search Console integrations use.

Scopes: ``gmail.send`` to send, ``gmail.readonly`` to detect replies and
opt-outs. ``gmail.readonly`` is a Google *restricted* scope: fine for the
Cloud project's test users, but a public launch needs Google verification and
a CASA security assessment.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import urlencode

import requests
from django.conf import settings

from apps.integrations.models import Integration

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
TIMEOUT_SECONDS = 20


class GmailError(Exception):
    """A Gmail call failed; the message is safe to store."""


@dataclass(frozen=True)
class SentMessage:
    message_id: str
    thread_id: str
    rfc_message_id: str  # the Message-ID header, used for In-Reply-To


@dataclass(frozen=True)
class ThreadMessage:
    sender: str
    snippet: str


def authorization_url(state: str) -> str:
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GROWTH_GMAIL_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(GMAIL_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    response = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GROWTH_GMAIL_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise GmailError(f"Google token exchange failed (HTTP {response.status_code}).")
    return response.json()


def _access_token(integration: Integration) -> str:
    # Shared Google credential helpers (same ones ga4.py / gsc.py use).
    from apps.integrations.views._shared import _build_credentials, _refresh_if_needed

    creds = _refresh_if_needed(integration, _build_credentials(integration, scopes=GMAIL_SCOPES))
    return creds.token


class GmailClient:
    def __init__(self, integration: Integration, token: str | None = None):
        self.integration = integration
        self._token = token

    def _headers(self) -> dict:
        if self._token is None:
            self._token = _access_token(self.integration)
        return {"Authorization": f"Bearer {self._token}"}

    def _call(self, method: str, path: str, **kwargs) -> dict:
        response = requests.request(
            method, f"{API}{path}", headers=self._headers(), timeout=TIMEOUT_SECONDS, **kwargs
        )
        if response.status_code not in (200, 201):
            raise GmailError(f"Gmail {path} failed (HTTP {response.status_code}).")
        return response.json()

    def profile_email(self) -> str:
        return self._call("GET", "/profile").get("emailAddress", "")

    def send(
        self, to: str, subject: str, body: str, *, thread_id: str = "", in_reply_to: str = ""
    ) -> SentMessage:
        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        payload = {"raw": raw, **({"threadId": thread_id} if thread_id else {})}
        sent = self._call("POST", "/messages/send", json=payload)
        meta = self._call(
            "GET", f"/messages/{sent['id']}", params={"format": "metadata", "metadataHeaders": "Message-ID"}
        )
        headers = {h["name"].lower(): h["value"] for h in meta.get("payload", {}).get("headers", [])}
        return SentMessage(sent["id"], sent.get("threadId", thread_id), headers.get("message-id", ""))

    def thread_messages(self, thread_id: str) -> list[ThreadMessage]:
        data = self._call(
            "GET", f"/threads/{thread_id}", params={"format": "metadata", "metadataHeaders": "From"}
        )
        out = []
        for msg in data.get("messages", []):
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            out.append(
                ThreadMessage(
                    sender=parseaddr(headers.get("from", ""))[1].lower(), snippet=msg.get("snippet", "")
                )
            )
        return out
