"""Who can apply a fix, and how.

The registry that makes remediation provider-agnostic. A provider registers an
adapter here; the orchestration never names a vendor.

Why this exists: "apply a fix to the customer's site" was implemented twice, once
per provider - ``analyzer.AutoFixJob`` (WordPress/Shopify, via integrations) and
``github_agent.GithubFixJob`` (GitHub). Adding Framer would have made a third.
See docs/app-boundaries.md.

The contract is deliberately small. Everything about *what* to change lives in
``services/agent.py`` and ``services/fixers.py`` and is already vendor-free; a
provider only has to answer three questions:

    make_client(integration)  - give me something I can read the site through
    profile(client)           - describe the target so the planner can reason
    apply(client, edits, ...) - make the change, and tell me what happened

``apply`` returns a plain dict rather than typed fields on purpose. GitHub
reports ``{"pr_url", "branch"}``; WordPress reports ``{"post_id", "revision"}``.
Provider-shaped output is **data, not schema** - that is what lets one FixJob
table serve every provider instead of one table per vendor.

Adding Framer is therefore one file: implement the three methods in
``apps/integrations/framer/remediation.py`` and register it from that app's
``AppConfig.ready()``. No new app, no new model, no migration.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger("apps")


class RemediationProvider(Protocol):
    """What a provider must implement to apply fixes."""

    #: Must match a value of ``integrations.Integration.Provider``.
    name: str

    def make_client(self, integration) -> Any:
        """A client the planner can read the target through.

        Must expose ``get_file(path)``, ``get_tree(ref)`` and ``search_code(query)``.
        ``search_code`` may return ``[]`` when the provider has no search - the
        planner falls back to matching against the file tree.
        """
        ...

    def profile(self, client) -> dict:
        """Describe the target (framework, default branch, layout) for the planner."""
        ...

    def apply(self, client, edits: list, *, title: str, body: str) -> dict:
        """Apply ``edits`` and return a provider-shaped result dict.

        Raise on failure. The caller records the exception against the job rather
        than guessing whether a partial write landed.
        """
        ...


_providers: dict[str, RemediationProvider] = {}


def register(provider: RemediationProvider) -> None:
    """Install an adapter. Called from the provider app's ``AppConfig.ready()``."""
    _providers[provider.name] = provider


def reset() -> None:
    """Drop all adapters. For tests exercising the unregistered path."""
    _providers.clear()


def available() -> list[str]:
    """Provider names that can currently apply a fix."""
    return sorted(_providers)


def get(name: str) -> RemediationProvider | None:
    """The adapter for ``name``, or None when that provider cannot apply fixes.

    None is a normal answer: a deployment without the GitHub App installed simply
    has no GitHub adapter, and the caller should report "no provider" rather than
    fail. Never raises.
    """
    provider = _providers.get((name or "").strip().lower())
    if provider is None:
        logger.info(
            "no remediation provider for %r (available: %s)", name, available() or "none"
        )
    return provider


def for_integration(integration) -> RemediationProvider | None:
    """The adapter matching ``integration.provider``."""
    return get(getattr(integration, "provider", ""))
