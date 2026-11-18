"""A brand's own Hunter or Apollo account.

Two ways to connect, both stored encrypted on the brand's Integration row:
- Sign in (OAuth, see ``oauth.py``): the usual way, like Claude and ChatGPT
  connectors. ``metadata["auth"] == "oauth"``.
- Paste an API key: checked with the provider first; only its last four
  characters are ever shown.
Lookups use the brand's own account first and Signalor's shared key (from
settings) only when the brand hasn't connected one.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db import transaction

from apps.integrations.models import Integration

from . import apollo, hunter

PROVIDERS = (hunter.PROVIDER, apollo.PROVIDER)
LABELS = {hunter.PROVIDER: "Hunter", apollo.PROVIDER: "Apollo"}
_MODULES = {hunter.PROVIDER: hunter, apollo.PROVIDER: apollo}
_SHARED_KEY_SETTINGS = {hunter.PROVIDER: "HUNTER_API_KEY", apollo.PROVIDER: "APOLLO_API_KEY"}
HINT_LENGTH = 4
MAX_KEY_LENGTH = 200
METHOD_OAUTH = "oauth"
METHOD_API_KEY = "api_key"
CREDENTIAL_METADATA = ("key_hint", "auth", "client_id")


def _shared_key(provider: str) -> str:
    return getattr(settings, _SHARED_KEY_SETTINGS[provider], "") or ""


def _account(organization, provider: str) -> Integration | None:
    if organization is None:
        return None
    return (
        Integration.objects.filter(organization=organization, provider=provider, is_active=True)
        .exclude(access_token_encrypted="")
        .first()
    )


def method_of(account: Integration) -> str:
    """How the brand connected: signed in (OAuth), or pasted an API key."""
    return account.metadata.get("auth", METHOD_API_KEY)


@dataclass(frozen=True)
class Connection:
    """What a lookup should use: a signed-in account, or an API key."""

    method: str
    api_key: str = ""
    integration: Integration | None = None


def connection_for(organization, provider: str) -> Connection | None:
    """The brand's own account first, then Signalor's shared key, else None."""
    account = _account(organization, provider)
    if account is not None:
        if method_of(account) == METHOD_OAUTH:
            return Connection(METHOD_OAUTH, integration=account)
        return Connection(METHOD_API_KEY, api_key=account.get_access_token())
    shared = _shared_key(provider)
    return Connection(METHOD_API_KEY, api_key=shared) if shared else None


def key_for(organization, provider: str) -> str:
    """The API key a lookup would use ("" when signed in or nothing is set up)."""
    connection = connection_for(organization, provider)
    return connection.api_key if connection else ""


def _entry(organization, provider: str) -> dict:
    account = _account(organization, provider)
    if account:
        source, method, hint = "account", method_of(account), account.metadata.get("key_hint", "")
    else:
        source, method, hint = ("signalor" if _shared_key(provider) else "none"), "", ""
    return {
        "provider": provider,
        "label": LABELS[provider],
        "connected": account is not None,
        "method": method,
        "key_hint": hint,
        "source": source,
    }


def describe(organization) -> list[dict]:
    """What the brand sees: connected or not, how, the key's last four, whose account is used."""
    return [_entry(organization, provider) for provider in PROVIDERS]


def _forget_credentials(account: Integration) -> None:
    account.access_token_encrypted = ""
    account.refresh_token_encrypted = ""
    account.token_expiry = None
    account.metadata = {k: v for k, v in account.metadata.items() if k not in CREDENTIAL_METADATA}


def connect(organization, provider: str, api_key: str) -> None:
    """Check the key with the provider (raises FinderError), then store it encrypted."""
    _MODULES[provider].check_key(api_key)  # network call, outside any transaction
    with transaction.atomic():
        account, _ = Integration.objects.select_for_update().get_or_create(
            organization=organization, provider=provider
        )
        _forget_credentials(account)  # replaces a sign-in, if there was one
        account.set_access_token(api_key)
        account.is_active = True
        account.metadata = {**account.metadata, "auth": METHOD_API_KEY, "key_hint": api_key[-HINT_LENGTH:]}
        account.save()


def disconnect(organization, provider: str) -> bool:
    """Forget the brand's key or sign-in tokens. Returns False when nothing was connected."""
    with transaction.atomic():
        account = (
            Integration.objects.select_for_update()
            .filter(organization=organization, provider=provider)
            .first()
        )
        if account is None:
            return False
        _forget_credentials(account)
        account.is_active = False
        account.save()
        return True
