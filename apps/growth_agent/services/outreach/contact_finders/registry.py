"""Which contact finders are usable for a brand, and asking all of them at once.

A finder is usable when the brand signed in to it, pasted its own API key, or
Signalor has a shared key for it (see ``accounts.connection_for``). Signed-in
accounts are reached through the provider's MCP server; keys through its REST API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import accounts, apollo, hunter, mcp_finders
from .base import Candidate, FinderError

logger = logging.getLogger("apps")
LABELS = accounts.LABELS
_MODULES = {hunter.PROVIDER: hunter, apollo.PROVIDER: apollo}


def configured(organization=None) -> list[str]:
    """Finders usable for this brand, in the order they are asked."""
    return [p for p in accounts.PROVIDERS if accounts.connection_for(organization, p)]


@dataclass
class FindResult:
    candidates: list[Candidate] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _find_with(provider: str, connection: accounts.Connection, domain: str) -> list[Candidate]:
    if connection.method == accounts.METHOD_OAUTH:
        return mcp_finders.find(connection.integration, domain)
    return _MODULES[provider].find(domain, connection.api_key)


def find_all(domain: str, organization=None) -> FindResult:
    """Ask every usable finder. One failing doesn't hide the others' answers."""
    result = FindResult(providers=configured(organization))
    for provider in result.providers:
        try:
            connection = accounts.connection_for(organization, provider)
            result.candidates.extend(_find_with(provider, connection, domain))
        except FinderError as exc:
            logger.warning("contact finder %s failed for %s: %s", provider, domain, exc)
            result.errors.append(provider)
    return result


def email_for(candidate: dict, organization=None) -> str:
    """The candidate's email, revealing it with Apollo when needed (1 credit)."""
    if candidate.get("email"):
        return candidate["email"]
    if candidate.get("provider") != apollo.PROVIDER:
        raise FinderError("This contact has no email.")
    connection = accounts.connection_for(organization, apollo.PROVIDER)
    if connection is None:
        raise FinderError("Apollo isn't connected any more.")
    if connection.method == accounts.METHOD_OAUTH:
        return mcp_finders.reveal(connection.integration, candidate["ref"])
    return apollo.reveal(candidate["ref"], connection.api_key)
