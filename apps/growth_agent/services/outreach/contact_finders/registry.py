"""Which contact finders are usable for a brand, and asking all of them at once.

A finder is usable when the brand connected its own account, or Signalor has a
shared key for it (see ``accounts.key_for``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import accounts, apollo, hunter
from .base import Candidate, FinderError

logger = logging.getLogger("apps")
LABELS = accounts.LABELS
_MODULES = {hunter.PROVIDER: hunter, apollo.PROVIDER: apollo}


def configured(organization=None) -> list[str]:
    """Finders with a key for this brand, in the order they are asked."""
    return [p for p in accounts.PROVIDERS if accounts.key_for(organization, p)]


@dataclass
class FindResult:
    candidates: list[Candidate] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def find_all(domain: str, organization=None) -> FindResult:
    """Ask every usable finder. One failing doesn't hide the others' answers."""
    result = FindResult(providers=configured(organization))
    for provider in result.providers:
        try:
            key = accounts.key_for(organization, provider)
            result.candidates.extend(_MODULES[provider].find(domain, key))
        except FinderError as exc:
            logger.warning("contact finder %s failed for %s: %s", provider, domain, exc)
            result.errors.append(provider)
    return result


def email_for(candidate: dict, organization=None) -> str:
    """The candidate's email, revealing it with Apollo when needed (1 credit)."""
    if candidate.get("email"):
        return candidate["email"]
    if candidate.get("provider") == apollo.PROVIDER:
        return apollo.reveal(candidate["ref"], accounts.key_for(organization, apollo.PROVIDER))
    raise FinderError("This contact has no email.")
