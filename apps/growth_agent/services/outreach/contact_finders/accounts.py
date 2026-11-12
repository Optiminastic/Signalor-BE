"""A brand's own Hunter or Apollo account.

The owner's API key is checked with the provider, then stored encrypted on the
brand's Integration row (like Gmail tokens). Only its last four characters are
ever shown. Lookups use the brand's own key first and Signalor's shared key
(from settings) only when the brand hasn't connected one.
"""

from __future__ import annotations

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


def key_for(organization, provider: str) -> str:
    """The brand's own key if connected, else Signalor's shared key, else ""."""
    account = _account(organization, provider)
    return account.get_access_token() if account else _shared_key(provider)


def describe(organization) -> list[dict]:
    """What the brand sees: connected or not, the key's last four, whose key is used."""
    entries = []
    for provider in PROVIDERS:
        account = _account(organization, provider)
        if account:
            source, hint = "account", account.metadata.get("key_hint", "")
        else:
            source, hint = ("signalor" if _shared_key(provider) else "none"), ""
        entries.append(
            {
                "provider": provider,
                "label": LABELS[provider],
                "connected": account is not None,
                "key_hint": hint,
                "source": source,
            }
        )
    return entries


def connect(organization, provider: str, api_key: str) -> None:
    """Check the key with the provider (raises FinderError), then store it encrypted."""
    _MODULES[provider].check_key(api_key)  # network call, outside any transaction
    with transaction.atomic():
        account, _ = Integration.objects.select_for_update().get_or_create(
            organization=organization, provider=provider
        )
        account.set_access_token(api_key)
        account.is_active = True
        account.metadata = {**account.metadata, "key_hint": api_key[-HINT_LENGTH:]}
        account.save()


def disconnect(organization, provider: str) -> bool:
    """Forget the brand's key. Returns False when nothing was connected."""
    with transaction.atomic():
        account = (
            Integration.objects.select_for_update()
            .filter(organization=organization, provider=provider)
            .first()
        )
        if account is None:
            return False
        account.access_token_encrypted = ""
        account.is_active = False
        account.metadata = {k: v for k, v in account.metadata.items() if k != "key_hint"}
        account.save()
        return True
