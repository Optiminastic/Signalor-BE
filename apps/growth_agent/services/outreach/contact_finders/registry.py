"""Which contact finders are set up (by API key), and asking all of them at once."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.conf import settings

from . import apollo, hunter
from .base import Candidate, FinderError

logger = logging.getLogger("apps")
LABELS = {hunter.PROVIDER: "Hunter", apollo.PROVIDER: "Apollo"}


def _key(provider: str) -> str:
    setting = {hunter.PROVIDER: "HUNTER_API_KEY", apollo.PROVIDER: "APOLLO_API_KEY"}[provider]
    return getattr(settings, setting, "") or ""


def configured() -> list[str]:
    """Finders with an API key, in the order they are asked."""
    return [p for p in (hunter.PROVIDER, apollo.PROVIDER) if _key(p)]


@dataclass
class FindResult:
    candidates: list[Candidate] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def find_all(domain: str) -> FindResult:
    """Ask every configured finder. One failing doesn't hide the others' answers."""
    result = FindResult(providers=configured())
    modules = {hunter.PROVIDER: hunter, apollo.PROVIDER: apollo}
    for provider in result.providers:
        try:
            result.candidates.extend(modules[provider].find(domain, _key(provider)))
        except FinderError as exc:
            logger.warning("contact finder %s failed for %s: %s", provider, domain, exc)
            result.errors.append(provider)
    return result


def email_for(candidate: dict) -> str:
    """The candidate's email, revealing it with Apollo when needed (1 credit)."""
    if candidate.get("email"):
        return candidate["email"]
    if candidate.get("provider") == apollo.PROVIDER:
        return apollo.reveal(candidate["ref"], _key(apollo.PROVIDER))
    raise FinderError("This contact has no email.")
