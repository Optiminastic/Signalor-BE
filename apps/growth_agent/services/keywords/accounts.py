"""
Keyword tools in the + panel, and which one the weekly keyword read uses.

- Signalor keyword data (DataForSEO): built in, nothing to connect.
- Google Search Console: connected on the Visibility page; its top queries show
  in the Keywords tab (real clicks and impressions, no extra cost).
- Semrush / Ahrefs: the brand's own API key, checked with the provider before it
  is saved (encrypted; only the last 4 characters are ever shown). When one is
  connected, keyword reads use it instead of the built-in data.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction

from apps.integrations.models import GSCDataSnapshot, Integration

from . import providers

KEY_PROVIDERS = (providers.SEMRUSH, providers.AHREFS)
HINT_LENGTH = 4
MAX_KEY_LENGTH = 200
BUILT_IN = "signalor"
SEARCH_CONSOLE = "search_console"


def _account(organization, provider: str) -> Integration | None:
    return (
        Integration.objects.filter(organization=organization, provider=provider, is_active=True)
        .exclude(access_token_encrypted="")
        .first()
    )


def _search_console(organization) -> Integration | None:
    return Integration.objects.filter(
        organization=organization, provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE, is_active=True
    ).first()


def own_source(organization) -> tuple[str, str] | None:
    """(provider, api_key) of the brand's own keyword account, Semrush first; None for built-in."""
    for provider in KEY_PROVIDERS:
        account = _account(organization, provider)
        if account is not None:
            return provider, account.get_access_token()
    return None


def search_console_queries(organization) -> list[dict]:
    """Top Google queries from the latest Search Console sync (stored; no network)."""
    integration = _search_console(organization)
    if integration is None:
        return []
    snapshot = GSCDataSnapshot.objects.filter(integration=integration, sync_status="complete").first()
    return list(snapshot.top_queries) if snapshot else []


def describe(organization) -> list[dict]:
    """Every keyword tool for the + panel: connected or not, and which one reads keywords."""
    own = own_source(organization)
    in_use = own[0] if own else BUILT_IN
    tools = [
        {
            "provider": BUILT_IN,
            "label": "Signalor keyword data",
            "kind": "built_in",
            "connected": bool(getattr(settings, "DATAFORSEO_LOGIN", "")),
            "in_use": in_use == BUILT_IN,
            "key_hint": "",
        },
        {
            "provider": SEARCH_CONSOLE,
            "label": "Google Search Console",
            "kind": "elsewhere",
            "connected": _search_console(organization) is not None,
            "in_use": False,
            "key_hint": "",
        },
    ]
    for provider in KEY_PROVIDERS:
        account = _account(organization, provider)
        tools.append(
            {
                "provider": provider,
                "label": providers.LABELS[provider],
                "kind": "api_key",
                "connected": account is not None,
                "in_use": in_use == provider,
                "key_hint": account.metadata.get("key_hint", "") if account else "",
            }
        )
    return tools


def connect(organization, provider: str, api_key: str) -> None:
    """Check the key with the provider (raises ProviderError), then store it encrypted."""
    providers.CHECKS[provider](api_key)  # network call, outside any transaction
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
        if account is None or not account.access_token_encrypted:
            return False
        account.access_token_encrypted = ""
        account.is_active = False
        account.metadata = {k: v for k, v in account.metadata.items() if k != "key_hint"}
        account.save()
        return True
