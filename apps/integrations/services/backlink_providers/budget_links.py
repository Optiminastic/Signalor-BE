"""
BudgetLinks backlink-provider client.

Mock reseller for low-cost placements — niche guest posts, profile/citation
links, and small-publication mentions, all priced under $50. Intended as the
budget tier alongside FATJOE's premium catalog. Switch to live mode by setting
BUDGETLINKS_API_KEY in the environment.
"""
from __future__ import annotations

import logging
import secrets
from typing import Iterable

from django.conf import settings

from . import BacklinkProviderClient, CatalogProduct, OrderResult, register

logger = logging.getLogger("apps")


def _mock_catalog() -> list[CatalogProduct]:
    """Curated budget-tier sample. Real reseller catalogs look very similar."""
    rows = [
        # (domain, title, da, rank, traffic, type, wholesale, retail, niches, country)
        ("dev.to", "DEV Community — Developer guest article", 76, 590, 5_400_000,
         "guest_post", 800, 1_500, ["dev", "tech", "saas"], "US"),
        ("hackernoon.com", "HackerNoon — Tech/startup guest post", 78, 620, 4_100_000,
         "guest_post", 1_900, 2_900, ["tech", "startup", "ai"], "US"),
        ("substack.com", "Substack — Niche newsletter feature", 91, 830, 95_000_000,
         "guest_post", 1_500, 2_500, ["business", "lifestyle", "creator"], "US"),
        ("lifehack.org", "Lifehack — Productivity guest post", 73, 560, 1_200_000,
         "guest_post", 1_200, 1_900, ["lifestyle", "productivity"], "US"),
        ("elephantjournal.com", "Elephant Journal — Wellness contribution", 75, 580, 1_900_000,
         "guest_post", 1_500, 2_500, ["wellness", "lifestyle", "health"], "US"),
        ("tinybuddha.com", "Tiny Buddha — Mindful living guest post", 71, 540, 920_000,
         "guest_post", 1_900, 2_900, ["wellness", "lifestyle", "mindfulness"], "US"),
        ("addictedtosuccess.com", "Addicted2Success — Self-improvement post", 65, 450, 480_000,
         "guest_post", 1_200, 1_900, ["business", "self-help"], "US"),
        ("selfgrowth.com", "SelfGrowth — Authority article", 70, 510, 720_000,
         "guest_post", 1_200, 1_900, ["self-help", "wellness"], "US"),
        ("ehow.com", "eHow — How-to niche edit", 84, 690, 6_800_000,
         "niche_edit", 1_900, 2_900, ["lifestyle", "diy", "general"], "US"),
        ("about.me", "About.me — Personal brand profile", 88, 740, 2_100_000,
         "citation", 300, 700, ["profile", "branding"], "US"),
        ("behance.net", "Behance — Verified portfolio profile", 92, 820, 28_000_000,
         "citation", 400, 900, ["design", "creator", "profile"], "US"),
        ("dribbble.com", "Dribbble — Designer profile + project", 89, 770, 12_000_000,
         "citation", 400, 900, ["design", "creator", "profile"], "US"),
        ("issuu.com", "Issuu — Publication profile + PDF", 92, 810, 38_000_000,
         "citation", 400, 900, ["publishing", "profile"], "US"),
        ("gravatar.com", "Gravatar — Verified author profile", 96, 870, 31_000_000,
         "citation", 200, 500, ["profile", "branding"], "US"),
        ("medium.com", "Medium — Small publication contributor post", 95, 940, 180_000_000,
         "guest_post", 900, 1_500, ["general", "writing"], "US"),
        ("bloglovin.com", "Bloglovin — Featured blog placement", 80, 620, 2_400_000,
         "citation", 1_200, 1_900, ["lifestyle", "fashion"], "US"),
        # Extra $10–$30 tier
        ("hubpages.com", "HubPages — Niche topic article", 88, 750, 8_900_000,
         "guest_post", 700, 1_200, ["general", "lifestyle"], "US"),
        ("flipboard.com", "Flipboard — Curated magazine feature", 91, 820, 24_000_000,
         "citation", 800, 1_500, ["general", "curation"], "US"),
        ("scoop.it", "Scoop.it — Curated topic placement", 90, 800, 1_400_000,
         "citation", 800, 1_500, ["general", "curation"], "US"),
        ("quora.com", "Quora — Niche Space contribution", 92, 870, 540_000_000,
         "guest_post", 1_200, 1_900, ["q-and-a", "general"], "US"),
        ("crunchbase.com", "Crunchbase — Company profile boost", 92, 850, 41_000_000,
         "citation", 1_200, 1_900, ["startup", "saas", "profile"], "US"),
        ("wellfound.com", "Wellfound — Startup profile placement", 80, 640, 2_800_000,
         "citation", 1_200, 1_900, ["startup", "profile"], "US"),
        ("f6s.com", "F6S — Startup founder profile + listing", 75, 580, 920_000,
         "citation", 1_500, 2_200, ["startup", "profile"], "US"),
        ("goodreads.com", "Goodreads — Author profile + book listing", 91, 830, 26_000_000,
         "citation", 1_700, 2_500, ["books", "author", "profile"], "US"),
        ("thriveglobal.com", "Thrive Global — Wellness/business contribution", 81, 660, 1_800_000,
         "guest_post", 1_700, 2_500, ["wellness", "business", "lifestyle"], "US"),
        ("sitepronews.com", "SitePronews — Marketing/SEO guest article", 70, 510, 320_000,
         "guest_post", 1_200, 1_900, ["marketing", "seo", "business"], "US"),
    ]
    out: list[CatalogProduct] = []
    for (domain, title, da, rank, traffic, link_type, wholesale, retail, niches, country) in rows:
        out.append(
            CatalogProduct(
                sku=f"budget-{domain}",
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
                lead_time_days=5,
                extras={},
            )
        )
    return out


@register
class BudgetLinksClient(BacklinkProviderClient):
    slug = "budget_links"
    display_name = "BudgetLinks"

    def __init__(self) -> None:
        self.api_key = getattr(settings, "BUDGETLINKS_API_KEY", "") or ""
        self.api_base = (
            getattr(settings, "BUDGETLINKS_API_BASE", "")
            or "https://api.budgetlinks.example/v1"
        )
        self.is_mock = not self.api_key

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

        raise NotImplementedError(
            "BudgetLinks live mode not wired yet. Add the HTTP call here when "
            "credentials are available."
        )

    def place_order(
        self,
        *,
        sku: str,
        target_url: str,
        anchor_text: str,
        notes: str = "",
    ) -> OrderResult:
        if self.is_mock:
            fake_id = f"bl_mock_{secrets.token_hex(6)}"
            return OrderResult(provider_order_id=fake_id, status="queued")

        raise NotImplementedError("BudgetLinks live order placement not wired yet.")

    def get_status(self, *, provider_order_id: str) -> OrderResult:
        if self.is_mock:
            return OrderResult(provider_order_id=provider_order_id, status="queued")

        raise NotImplementedError("BudgetLinks live status polling not wired yet.")
