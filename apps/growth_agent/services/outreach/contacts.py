"""
Find a real, published contact email for an outreach target. Never guesses.

Looks at the cited page first (author mailto links), then the site's usual
contact pages. Only addresses on the target's own domain are accepted, and
role addresses that should never get pitches (privacy, abuse, noreply...)
are rejected. Parsing is pure; fetching is injected so it is testable.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests

FETCH_TIMEOUT_SECONDS = 10
MAX_PAGE_BYTES = 1_500_000
CONTACT_PATHS = ("/contact", "/contact-us", "/about", "/write-for-us", "/advertise", "/press")
USER_AGENT = "Mozilla/5.0 (compatible; SignalorGrowthAgent/1.0)"
BLOCKED_LOCAL_PARTS = (
    "noreply",
    "no-reply",
    "donotreply",
    "privacy",
    "abuse",
    "legal",
    "security",
    "dmca",
    "careers",
    "jobs",
    "billing",
    "invoice",
    "unsubscribe",
    "postmaster",
    "webmaster",
    "gdpr",
    "support",
    "help",
)
PREFERRED_LOCAL_PARTS = (
    "editor",
    "editorial",
    "content",
    "partnerships",
    "partner",
    "press",
    "pr",
    "hello",
    "info",
)
_MAILTO = re.compile(r'mailto:([^"\'?\s>]+)', re.IGNORECASE)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}\b")
_IMAGE_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")

Fetch = Callable[[str], str | None]


@dataclass(frozen=True)
class Contact:
    email: str
    source_url: str


def default_fetch(url: str) -> str | None:
    try:
        response = requests.get(url, timeout=FETCH_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
    except requests.RequestException:
        return None
    if response.status_code != 200 or "html" not in response.headers.get("Content-Type", "html"):
        return None
    return response.text[:MAX_PAGE_BYTES]


def _registrable(host: str) -> str:
    parts = host.lower().removeprefix("www.").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _acceptable(email: str, site_domain: str) -> bool:
    local, _, domain = email.lower().partition("@")
    if not local or domain.endswith(_IMAGE_SUFFIX) or local.endswith(_IMAGE_SUFFIX):
        return False
    if _registrable(domain) != _registrable(site_domain):
        return False
    return not any(blocked in local for blocked in BLOCKED_LOCAL_PARTS)


def extract_emails(page_html: str, site_domain: str) -> list[str]:
    """Acceptable emails in the page, mailto links first, deduplicated, preferred roles first."""
    found = [m.strip().lower() for m in _MAILTO.findall(page_html)]
    found += [m.lower() for m in _EMAIL.findall(page_html)]
    unique = list(dict.fromkeys(e for e in found if _acceptable(e, site_domain)))
    return sorted(unique, key=lambda e: 0 if e.split("@")[0] in PREFERRED_LOCAL_PARTS else 1)


def candidate_urls(target_url: str, domain: str) -> list[str]:
    origin = f"https://{domain}"
    urls = [target_url] if target_url else []
    urls += [urljoin(origin, path) for path in CONTACT_PATHS]
    return list(dict.fromkeys(urls))


def discover_contact(target_url: str, domain: str, fetch: Fetch = default_fetch) -> Contact | None:
    site_domain = domain or urlparse(target_url).hostname or ""
    if not site_domain:
        return None
    for url in candidate_urls(target_url, site_domain):
        page = fetch(url)
        if not page:
            continue
        emails = extract_emails(page, site_domain)
        if emails:
            return Contact(email=emails[0], source_url=url)
    return None
