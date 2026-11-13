"""Sign in with Hunter or Apollo, the way Claude and ChatGPT connectors do.

Both providers run a remote MCP server protected by OAuth 2.1 with dynamic
client registration and PKCE (endpoints read from their published
``/.well-known/oauth-authorization-server`` metadata, Nov 2026):

1. ``start``: register Signalor as a public client, then send the owner to the
   provider's own login and consent page. No password ever reaches Signalor.
2. ``finish``: the provider redirects back with a code; exchange it (with the
   PKCE verifier) for an access and refresh token, stored encrypted on the
   brand's Integration row.
3. ``access_token``: refresh the token when it is about to expire.

The in-flight details (brand, owner, PKCE verifier, client id, where to send
the browser back) travel inside the ``state`` value, encrypted with the same
key as stored tokens, and expire after ten minutes. Nothing depends on a
per-process cache, so start and callback may hit different web workers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode, urlparse

import requests
from cryptography.fernet import InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.integrations.models import Integration, decrypt_token, encrypt_token

from ....models import GrowthProgram
from . import apollo, hunter
from .base import TIMEOUT_SECONDS, FinderError

CLIENT_NAME = "signalor.ai"
STATE_MAX_AGE_SECONDS = 600
REFRESH_MARGIN = timedelta(seconds=60)
CALLBACK_PATH = "/api/growth/oauth/finders/callback/"
RETURN_PATH = re.compile(r"^/dashboard/[A-Za-z0-9_-]{1,64}/agent-mode(/[A-Za-z0-9_-]{1,64})?/?$")


@dataclass(frozen=True)
class OAuthServer:
    provider: str
    mcp_url: str
    resource: str
    authorize_url: str
    token_url: str
    register_url: str
    scopes: tuple[str, ...]


SERVERS = {
    hunter.PROVIDER: OAuthServer(
        provider=hunter.PROVIDER,
        mcp_url="https://mcp.hunter.io/mcp",
        resource="https://mcp.hunter.io",
        authorize_url="https://hunter.io/oauth/authorize",
        token_url="https://hunter.io/oauth/token",
        register_url="https://hunter.io/oauth/register",
        scopes=("read",),
    ),
    apollo.PROVIDER: OAuthServer(
        provider=apollo.PROVIDER,
        mcp_url="https://mcp.apollo.io/mcp",
        resource="https://mcp.apollo.io/mcp",
        authorize_url="https://mcp.apollo.io/mcp/oauth_metadata/redirect_to_authorize",
        token_url="https://mcp.apollo.io/api/v1/oauth/token",
        register_url="https://mcp.apollo.io/api/v1/oauth/applications/register_oauth_client",
        # Only what contact finding needs: search people, reveal one email.
        scopes=("mcp_scope", "read_user_profile", "mixed_people_api_search", "people_match"),
    ),
}


class ConnectError(Exception):
    """Sign-in could not complete. ``code`` is safe to put in a redirect URL."""

    def __init__(self, code: str, return_url: str = ""):
        super().__init__(code)
        self.code = code
        self.return_url = return_url


def redirect_uri() -> str:
    return f"{settings.BACKEND_BASE_URL}{CALLBACK_PATH}"


def valid_return_url(url: str) -> bool:
    """Only our own app's Agent mode pages, on a configured origin (no open redirect)."""
    parsed = urlparse(url or "")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin in settings.GROWTH_APP_ORIGINS and bool(RETURN_PATH.match(parsed.path))


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _post_json(url: str, **kwargs) -> dict:
    try:
        reply = requests.post(url, timeout=TIMEOUT_SECONDS, **kwargs)
    except requests.RequestException as exc:
        raise FinderError(f"couldn't reach {urlparse(url).netloc}: {type(exc).__name__}") from exc
    if not reply.ok:
        raise FinderError(f"{urlparse(url).netloc} answered HTTP {reply.status_code}")
    return reply.json()


def _register(server: OAuthServer) -> str:
    """Dynamic client registration (RFC 7591) as a public client using PKCE."""
    body = {
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect_uri()],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": " ".join(server.scopes),
    }
    client_id = _post_json(server.register_url, json=body).get("client_id")
    if not client_id:
        raise FinderError("registration returned no client id")
    return client_id


def start(program: GrowthProgram, provider: str, owner_email: str, return_url: str) -> str:
    """The provider's login URL for this brand's owner. Raises FinderError."""
    server = SERVERS[provider]
    client_id = _register(server)
    verifier, challenge = _pkce_pair()
    state = encrypt_token(
        json.dumps(
            {
                "program": program.id,
                "email": owner_email,
                "provider": provider,
                "client_id": client_id,
                "verifier": verifier,
                "return_url": return_url,
                "issued": int(time.time()),
            }
        )
    )
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri(),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": " ".join(server.scopes),
        "resource": server.resource,
        "state": state,
    }
    return f"{server.authorize_url}?{urlencode(params)}"


def read_state(state: str) -> dict:
    try:
        data = json.loads(decrypt_token(state))
    except (InvalidToken, ValueError) as exc:
        raise ConnectError("invalid_state") from exc
    if time.time() - data.get("issued", 0) > STATE_MAX_AGE_SECONDS:
        raise ConnectError("expired", data.get("return_url", ""))
    if data.get("provider") not in SERVERS:
        raise ConnectError("invalid_state")
    return data


def _owned_program(data: dict) -> GrowthProgram:
    program = (
        GrowthProgram.objects.select_related("organization")
        .filter(pk=data.get("program"), organization__owner_email__iexact=data.get("email", ""))
        .first()
    )
    if program is None:
        raise ConnectError("not_owner", data["return_url"])
    return program


def _exchange(server: OAuthServer, data: dict, code: str) -> dict:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "client_id": data["client_id"],
        "code_verifier": data["verifier"],
        "resource": server.resource,
    }
    try:
        tokens = _post_json(server.token_url, data=form)
    except FinderError as exc:
        raise ConnectError("token_exchange", data["return_url"]) from exc
    if not tokens.get("access_token"):
        raise ConnectError("token_exchange", data["return_url"])
    return tokens


def _store(integration: Integration, tokens: dict) -> None:
    integration.set_access_token(tokens["access_token"])
    if tokens.get("refresh_token"):
        integration.set_refresh_token(tokens["refresh_token"])
    expires_in = tokens.get("expires_in")
    integration.token_expiry = timezone.now() + timedelta(seconds=int(expires_in)) if expires_in else None


def finish(code: str, state: str) -> tuple[GrowthProgram, dict]:
    """Exchange the code and store the tokens. Returns the program and the state data."""
    data = read_state(state)
    if not code:
        raise ConnectError("missing_code", data["return_url"])
    program = _owned_program(data)
    server = SERVERS[data["provider"]]
    tokens = _exchange(server, data, code)
    with transaction.atomic():
        integration, _ = Integration.objects.select_for_update().get_or_create(
            organization=program.organization, provider=server.provider
        )
        _store(integration, tokens)
        integration.is_active = True
        metadata = {k: v for k, v in integration.metadata.items() if k != "key_hint"}
        integration.metadata = {**metadata, "auth": "oauth", "client_id": data["client_id"]}
        integration.save()
    return program, data


def _refresh(integration: Integration) -> None:
    server = SERVERS[integration.provider]
    refresh_token = integration.get_refresh_token()
    if not refresh_token:
        raise FinderError(f"{server.provider} sign-in expired; reconnect it")
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": integration.metadata.get("client_id", ""),
        "resource": server.resource,
    }
    tokens = _post_json(server.token_url, data=form)
    if not tokens.get("access_token"):
        raise FinderError(f"{server.provider} sign-in expired; reconnect it")
    with transaction.atomic():
        locked = Integration.objects.select_for_update().get(pk=integration.pk)
        _store(locked, tokens)
        locked.save(
            update_fields=["access_token_encrypted", "refresh_token_encrypted", "token_expiry", "updated_at"]
        )
    integration.refresh_from_db()


def access_token(integration: Integration, *, force_refresh: bool = False) -> str:
    """A usable access token, refreshed first when it is (about to be) expired."""
    expiry = integration.token_expiry
    if force_refresh or (expiry is not None and expiry - REFRESH_MARGIN <= timezone.now()):
        _refresh(integration)
    return integration.get_access_token()
