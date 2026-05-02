"""
FATJOE backlink-provider client.

Currently runs in MOCK mode — it returns a hand-curated catalog and simulates
order fulfillment so the marketplace UI is fully usable without provider
credentials. Switch to live mode by setting FATJOE_API_KEY (and optionally
FATJOE_API_BASE) in the environment; the same class then talks to the real
reseller API. The view/service layer never needs to know which mode is active.
"""
from __future__ import annotations

import logging
import secrets
from typing import Iterable

from django.conf import settings

from . import BacklinkProviderClient, CatalogProduct, OrderResult, register

logger = logging.getLogger("apps")


def _mock_catalog() -> list[CatalogProduct]:
    """Curated sample so the UI shows realistic listings until real creds arrive."""
    rows = [
        # (domain, title, da, rank, traffic, type, wholesale, retail, niches, country)
        ("forbes.com", "Forbes — Sponsored business article", 95, 920, 240_000_000,
         "sponsored", 195_000, 295_000, ["business", "tech", "finance"], "US"),
        ("entrepreneur.com", "Entrepreneur — Guest contribution", 92, 880, 32_000_000,
         "guest_post", 89_000, 139_000, ["business", "startup"], "US"),
        ("medium.com", "Medium — Niche publication placement", 95, 940, 180_000_000,
         "guest_post", 14_900, 24_900, ["tech", "lifestyle", "business"], "US"),
        ("hubspot.com", "HubSpot blog — Niche edit", 92, 870, 28_000_000,
         "niche_edit", 64_900, 99_900, ["marketing", "saas"], "US"),
        ("searchenginejournal.com", "Search Engine Journal — Guest post", 87, 760, 4_200_000,
         "guest_post", 49_900, 79_900, ["seo", "marketing"], "US"),
        ("readwrite.com", "ReadWrite — Sponsored tech feature", 84, 720, 1_800_000,
         "sponsored", 39_900, 64_900, ["tech", "ai"], "US"),
        ("benzinga.com", "Benzinga — Finance news placement", 86, 740, 14_000_000,
         "sponsored", 44_900, 69_900, ["finance", "crypto"], "US"),
        ("yourstory.com", "YourStory — Startup feature", 80, 650, 6_500_000,
         "sponsored", 29_900, 49_900, ["startup", "business"], "IN"),
        ("inc42.com", "Inc42 — Indian startup ecosystem feature", 76, 590, 3_900_000,
         "sponsored", 24_900, 39_900, ["startup", "tech", "india"], "IN"),
        ("techcrunch.com", "TechCrunch — Niche edit (Crunchbase profile push)", 94, 910, 28_000_000,
         "niche_edit", 149_000, 219_000, ["tech", "startup"], "US"),
        ("dzone.com", "DZone — Developer guest article", 81, 680, 3_200_000,
         "guest_post", 19_900, 34_900, ["dev", "tech"], "US"),
        ("indiehackers.com", "Indie Hackers — Community profile + post", 76, 590, 1_400_000,
         "guest_post", 9_900, 19_900, ["startup", "saas", "indie"], "US"),
        ("producthunt.com", "Product Hunt — Featured launch listing", 90, 820, 9_800_000,
         "citation", 0, 9_900, ["saas", "startup"], "US"),
        ("g2.com", "G2 — Verified profile claim + review push", 91, 850, 17_000_000,
         "citation", 19_900, 39_900, ["saas", "reviews"], "US"),
        ("capterra.com", "Capterra — Listing claim + boost", 89, 790, 11_000_000,
         "citation", 14_900, 29_900, ["saas", "reviews"], "US"),
        ("trustpilot.com", "Trustpilot — Verified business profile", 92, 840, 23_000_000,
         "citation", 14_900, 24_900, ["reviews", "ecommerce"], "US"),
    ]
    out: list[CatalogProduct] = []
    for (domain, title, da, rank, traffic, link_type, wholesale, retail, niches, country) in rows:
        out.append(
            CatalogProduct(
                sku=f"fatjoe-{domain}",
                domain=domain,
                title=title,
                link_type=link_type,
                domain_authority=da,
                domain_rank=rank,
                monthly_traffic=traffic,
                niche_tags=list(niches),
                language="en",
                country=country,
                do_follow=True,
                wholesale_price_cents=wholesale,
                retail_price_cents=retail,
                currency="USD",
                lead_time_days=10,
                extras={},
            )
        )
    return out


@register
class FatjoeClient(BacklinkProviderClient):
    slug = "fatjoe"
    display_name = "FATJOE"

    def __init__(self) -> None:
        self.api_key = getattr(settings, "FATJOE_API_KEY", "") or ""
        self.api_base = (
            getattr(settings, "FATJOE_API_BASE", "")
            or "https://api.fatjoe.com/v1"
        )
        self.is_mock = not self.api_key

    # ── Catalog ─────────────────────────────────────────────────────────
    def list_products(self, *, niches: Iterable[str] = ()) -> list[CatalogProduct]:
        if self.is_mock:
            rows = _mock_catalog()
            niche_filter = {n.lower() for n in niches}
            if niche_filter:
                rows = [
                    r for r in rows
                    if any(t.lower() in niche_filter for t in r.niche_tags)
                ]
            return rows

        # Real implementation hook — see FATJOE reseller docs.
        # GET {self.api_base}/catalog?niche=...
        # Auth: Bearer {self.api_key}
        raise NotImplementedError(
            "FATJOE live mode not wired yet. Add the HTTP call here when "
            "credentials are available."
        )

    # ── Orders ──────────────────────────────────────────────────────────
    def place_order(
        self,
        *,
        sku: str,
        target_url: str,
        anchor_text: str,
        notes: str = "",
    ) -> OrderResult:
        if self.is_mock:
            # Pretend the provider accepted and queued the order.
            fake_id = f"fj_mock_{secrets.token_hex(6)}"
            return OrderResult(provider_order_id=fake_id, status="queued")

        raise NotImplementedError("FATJOE live order placement not wired yet.")

    def get_status(self, *, provider_order_id: str) -> OrderResult:
        if self.is_mock:
            # In mock mode we never auto-progress; the admin can flip the
            # local order to "delivered" via the API or fixture.
            return OrderResult(provider_order_id=provider_order_id, status="queued")

        raise NotImplementedError("FATJOE live status polling not wired yet.")
