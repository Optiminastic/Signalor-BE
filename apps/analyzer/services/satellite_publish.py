"""Publish one post to one of signalor.ai's satellite sites.

Posts live in the satellite blog store (``blog_store``, S3); each site renders
``<site domain>/<slug>`` from it. Shared by the manual "publish to network"
endpoint and the Growth Agent's satellite channel, so slugs, post fields and
live URLs are built in one place.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

from apps.analyzer import blog_store

SITES = ("research", "listicals", "market_trends", "comparison", "step_guide")
SPONSORED_REL = "sponsored noopener"
_ANCHOR = re.compile(r"<a\b([^>]*)>", re.IGNORECASE)
_HREF = re.compile(r"""\bhref\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)
_REL = re.compile(r"""\s*\brel\s*=\s*(["']).*?\1""", re.IGNORECASE | re.DOTALL)


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def sponsor_client_links(content_html: str, brand_url: str) -> str:
    """Every link to the client's site carries ``rel="sponsored noopener"`` (Google's
    rule for placed links). Links to other sites are left as they are."""
    brand_host = _host(brand_url or "")
    if not brand_host or not content_html:
        return content_html

    def mark(match: re.Match) -> str:
        attrs = match.group(1)
        href = _HREF.search(attrs)
        if not href or _host(href.group(2)) != brand_host:
            return match.group(0)
        return f'<a{_REL.sub("", attrs)} rel="{SPONSORED_REL}">'

    return _ANCHOR.sub(mark, content_html)


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
        "content_html": sponsor_client_links(content_html, brand_url),
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


def refresh_post(
    *, site: str, slug: str, title: str, content_html: str, brand_url: str, description: str = ""
) -> dict:
    """Replace an existing post's title, description and body in place (same address).

    Client links get ``rel="sponsored"`` as on publish. Returns the stored post with its ``url``.
    """
    if site not in SITES:
        raise ValueError(f"Unknown satellite site: {site}")
    fields = {
        "title": title[:300],
        "description": description[:2000],
        "content_html": sponsor_client_links(content_html, brand_url),
        "updated_at": timezone.now().isoformat(),
    }
    post = blog_store.update_post(site, slug, fields) or {}
    return {**post, "url": live_url(site, slug)}
