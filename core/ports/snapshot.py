"""Port: does this org expose a pre-rendered snapshot endpoint the crawler can use?

``analyzer.services.nextjs_snapshot`` reached into ``public_api.models`` for
``NextJsDeployment`` to answer this. That single import was the whole
``analyzer <-> public_api`` cycle (docs/modularization-plan.md §2.2), and it
pointed the wrong way: ``public_api`` is the outermost layer, so nothing below it
should import it.

Inverted here. ``public_api`` registers an adapter that knows about deployments;
``analyzer`` asks the port. Unregistered means "no snapshot available", which is
exactly the answer for a deployment that never registered one, so the crawler
falls back to fetching the live site.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger("apps")


class SnapshotProvider(Protocol):
    def get_config(self, run) -> dict | None:
        """``{"origin", "routes", "key_hash"}`` for the run's org, or None."""
        ...


_provider: SnapshotProvider | None = None


def register(provider: SnapshotProvider) -> None:
    global _provider
    _provider = provider


def reset() -> None:
    """Drop the adapter. For tests exercising the unregistered path."""
    global _provider
    _provider = None


def is_registered() -> bool:
    return _provider is not None


def get_config(run) -> dict | None:
    """Snapshot config for this run's org, or None when unavailable.

    None is the normal answer for most runs — the great majority of sites are not
    Next.js apps with a registered snapshot route.
    """
    if _provider is None:
        return None
    try:
        return _provider.get_config(run)
    except Exception:
        logger.warning("snapshot provider lookup failed", exc_info=True)
        return None
