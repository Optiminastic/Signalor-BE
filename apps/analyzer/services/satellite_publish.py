"""Publish one post to one of signalor.ai's satellite sites.

Posts live in the satellite blog store (``blog_store``, S3); each site renders
``<site domain>/<slug>`` from it. Shared by the manual "publish to network"
endpoint and the Growth Agent's satellite channel, so slugs, post fields and
live URLs are built in one place.
"""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone

from apps.analyzer import blog_store

SITES = ("research", "listicals", "market_trends", "comparison", "step_guide")


def site_domain(site: str) -> str:
    return (getattr(settings, "SATELLITE_SITES", {}).get(site) or "").rstrip("/")


def live_url(site: str, slug: str) -> str:
    domain = site_domain(site)
    return f"{domain}/{slug}" if domain else ""


def unique_slug(site: str, base: str) -> str:
    """``base``, or ``base-2``, ``base-3``... if that slug is taken on the site."""
    slug, n = base, 2
    while blog_store.slug_exists(site, slug):
        slug = f"{base}-{n}"
        n += 1
    return slug


def publish_post(
    *,
    site: str,
    slug: str,
    title: str,
    content_html: str,
    brand_url: str,
    brand_ref: str,
    description: str = "",
    source: str = "signalor",
    image_url: str = "",
    category: str = "",
) -> dict:
    """Store a published post under exactly ``slug`` and return it with its live ``url``."""
    if site not in SITES:
        raise ValueError(f"Unknown satellite site: {site}")
    now = timezone.now().isoformat()
    post = {
        "id": blog_store.new_id(),
        "site": site,
        "slug": slug,
        "title": title[:300],
        "description": description[:2000],
        "content_html": content_html,
        "image_url": image_url[:2048],
        "category": category[:80],
        "brand_url": brand_url,
        "brand_ref": brand_ref,
        "source": source,
        "status": "published",
        "published_at": now,
        "created_at": now,
    }
    blog_store.put_post(post)
    return {**post, "url": live_url(site, slug)}
