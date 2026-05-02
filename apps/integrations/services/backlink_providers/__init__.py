"""
Backlink-marketplace provider clients.

Each provider exposes a uniform interface so views/services don't care which
vendor fulfills an order. Add a new provider by implementing the
`BacklinkProviderClient` ABC and registering it in `get_client()`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable


@dataclass
class CatalogProduct:
    """Provider-agnostic catalog row used to upsert BacklinkProduct."""

    sku: str
    domain: str
    title: str
    link_type: str
    domain_authority: int | None
    domain_rank: int | None
    monthly_traffic: int | None
    niche_tags: list[str]
    language: str
    country: str
    do_follow: bool
    wholesale_price_cents: int
    retail_price_cents: int
    currency: str
    lead_time_days: int
    extras: dict


@dataclass
class OrderResult:
    provider_order_id: str
    status: str
    proof_url: str = ""
    error_message: str = ""


class BacklinkProviderClient(ABC):
    """All backlink providers conform to this interface."""

    slug: str
    display_name: str

    @abstractmethod
    def list_products(self, *, niches: Iterable[str] = ()) -> list[CatalogProduct]:
        """Fetch the buyable catalog from the provider."""

    @abstractmethod
    def place_order(
        self,
        *,
        sku: str,
        target_url: str,
        anchor_text: str,
        notes: str = "",
    ) -> OrderResult:
        """Place an order; raises on protocol errors, returns status otherwise."""

    @abstractmethod
    def get_status(self, *, provider_order_id: str) -> OrderResult:
        """Poll status; safe to call repeatedly."""


_REGISTRY: dict[str, type[BacklinkProviderClient]] = {}


def register(cls: type[BacklinkProviderClient]) -> type[BacklinkProviderClient]:
    _REGISTRY[cls.slug] = cls
    return cls


def get_client(slug: str) -> BacklinkProviderClient:
    if slug not in _REGISTRY:
        # Lazy import so providers self-register on first use.
        from . import fatjoe, budget_links  # noqa: F401
    if slug not in _REGISTRY:
        raise KeyError(f"No backlink provider registered for slug={slug!r}")
    return _REGISTRY[slug]()


def all_provider_slugs() -> list[str]:
    from . import fatjoe, budget_links  # noqa: F401
    return sorted(_REGISTRY.keys())
