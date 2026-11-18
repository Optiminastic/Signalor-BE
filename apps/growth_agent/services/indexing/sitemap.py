"""Read a site's sitemap: each page URL and when it last changed (``<lastmod>``).

Finds sitemaps from ``robots.txt`` (``Sitemap:`` lines), else ``/sitemap.xml``,
and follows one level of sitemap index. Every fetch goes through the SSRF guard
(the host comes from a stored brand URL, but DNS and redirects can point
anywhere), and XML is parsed without entities or network access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

import requests
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.timezone import is_naive, make_aware
from lxml import etree

from apps.analyzer.url_guard import SSRFValidationError, guarded_session

logger = logging.getLogger("apps")
TIMEOUT_SECONDS = 15
MAX_BYTES = 10 * 1024 * 1024
MAX_CHILD_SITEMAPS = 20
MAX_URLS = 5000
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, recover=True)


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    lastmod: datetime | None


def _origin(site_url: str) -> str:
    parsed = urlparse(site_url if "://" in site_url else f"https://{site_url}")
    return f"{parsed.scheme or 'https'}://{parsed.netloc}" if parsed.netloc else ""


def _get(session: requests.Session, url: str) -> bytes | None:
    try:
        reply = session.get(url, timeout=TIMEOUT_SECONDS, allow_redirects=True, stream=True)
        if reply.status_code != 200:
            return None
        body = reply.raw.read(MAX_BYTES + 1, decode_content=True)
        return body if len(body) <= MAX_BYTES else None
    except (SSRFValidationError, requests.RequestException) as exc:
        logger.info("sitemap fetch failed for %s: %s", url, type(exc).__name__)
        return None


def _sitemaps_from_robots(session: requests.Session, origin: str) -> list[str]:
    body = _get(session, f"{origin}/robots.txt")
    if not body:
        return []
    lines = body.decode("utf-8", errors="replace").splitlines()
    return [line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("sitemap:")]


def _lastmod(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    moment = parse_datetime(text)
    if moment is None:
        day = parse_date(text[:10])
        moment = datetime(day.year, day.month, day.day) if day else None
    if moment is not None and is_naive(moment):
        moment = make_aware(moment)
    return moment


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse(body: bytes) -> tuple[list[SitemapEntry], list[str]]:
    """Pages (with lastmod) and child sitemap URLs from one sitemap document."""
    try:
        root = etree.fromstring(body, _PARSER)
    except etree.XMLSyntaxError:
        return [], []
    if root is None:
        return [], []
    entries, children = [], []
    for node in root:
        fields = {_local(child.tag): (child.text or "").strip() for child in node}
        if not fields.get("loc"):
            continue
        if _local(root.tag) == "sitemapindex":
            children.append(fields["loc"])
        else:
            entries.append(SitemapEntry(fields["loc"], _lastmod(fields.get("lastmod"))))
    return entries, children


def fetch_entries(site_url: str) -> list[SitemapEntry]:
    """Every page listed in the site's sitemaps (capped), de-duplicated by URL."""
    origin = _origin(site_url)
    if not origin:
        return []
    session = guarded_session()
    try:
        queue = _sitemaps_from_robots(session, origin) or [f"{origin}/sitemap.xml"]
        found: dict[str, SitemapEntry] = {}
        for index, sitemap_url in enumerate(queue):
            if index >= MAX_CHILD_SITEMAPS or len(found) >= MAX_URLS:
                break
            body = _get(session, sitemap_url)
            entries, children = parse(body) if body else ([], [])
            queue.extend(children)
            for entry in entries:
                found.setdefault(entry.url, entry)
        return list(found.values())[:MAX_URLS]
    finally:
        session.close()
