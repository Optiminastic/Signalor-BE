"""
Technical checks a site must pass for AI engines to read it: robots.txt lets the
AI crawlers in, and the sitemap exists, has no broken or redirecting URLs, and is
kept up to date.

Each check is passed, failed or unknown (couldn't be checked: never reported as
passed or failed). Only failed checks become On-site tickets; passed ones are
recorded so the owner can see the checks ran.

The decisions are pure functions; ``run_checks`` does the fetching, through the
SSRF-guarded session (sitemap URLs come from the customer's site).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urljoin

import requests

from core import xml_safe

from ..url_guard import SSRFValidationError, guarded_session
from . import robots_rules

logger = logging.getLogger("apps")

PASSED, FAILED, UNKNOWN = "passed", "failed", "unknown"

# The crawlers a failed robots check is about (customers ask about these answers most).
REQUIRED_AI_BOTS = ("GPTBot", "PerplexityBot", "ClaudeBot")
TIMEOUT_SECONDS = 10
MAX_CHILD_SITEMAPS = 3  # a sitemap index can list hundreds; a sample is enough to judge
URL_SAMPLE = 30
STALE_AFTER_DAYS = 90
EXAMPLES_SHOWN = 5
_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


@dataclass(frozen=True)
class Check:
    code: str
    label: str
    status: str
    detail: str
    evidence: dict = field(default_factory=dict)


@dataclass
class Sitemap:
    """What was read from the site's sitemap(s)."""

    found: bool | None  # None: couldn't be fetched (unknown)
    source: str = ""
    urls: list[str] = field(default_factory=list)
    lastmods: list[datetime] = field(default_factory=list)


# ── Pure decisions ──────────────────────────────────────────────────────────


def robots_check(robots_txt: str | None, status: int | None) -> Check:
    """``status`` None = the fetch failed; 404 = no robots.txt (everything allowed)."""
    label = "AI crawlers allowed in robots.txt"
    if status is None:
        return Check("robots_ai_bots", label, UNKNOWN, "robots.txt couldn't be fetched.")
    if status == 404 or not (robots_txt or "").strip():
        return Check("robots_ai_bots", label, PASSED, "No robots.txt, so every crawler is allowed.")
    all_blocked = robots_rules.blocked(robots_txt, robots_rules.AI_BOTS)
    required = [b for b in REQUIRED_AI_BOTS if b in all_blocked]
    evidence = {"blocked": all_blocked, "checked": list(robots_rules.AI_BOTS)}
    if required:
        names = ", ".join(required)
        return Check("robots_ai_bots", label, FAILED, f"robots.txt blocks {names}.", evidence)
    return Check(
        "robots_ai_bots", label, PASSED, "GPTBot, PerplexityBot and ClaudeBot can crawl the site.", evidence
    )


def sitemap_found_check(sitemap: Sitemap) -> Check:
    label = "Sitemap found"
    if sitemap.found is None:
        return Check("sitemap_found", label, UNKNOWN, "The sitemap couldn't be fetched.")
    if not sitemap.found:
        return Check("sitemap_found", label, FAILED, "No sitemap.xml was found.")
    return Check(
        "sitemap_found", label, PASSED, f"{len(sitemap.urls)} URLs listed.", {"source": sitemap.source}
    )


def _url_check(code: str, label: str, sitemap: Sitemap, bad: list[str], checked: int, what: str) -> Check:
    if not sitemap.found or not checked:
        return Check(code, label, UNKNOWN, "Skipped: no sitemap URLs to check.")
    evidence = {"checked": checked, "count": len(bad), "examples": bad[:EXAMPLES_SHOWN]}
    if bad:
        return Check(code, label, FAILED, f"{len(bad)} of {checked} sitemap URLs checked {what}.", evidence)
    return Check(code, label, PASSED, f"None of the {checked} sitemap URLs checked {what}.", evidence)


def broken_urls_check(sitemap: Sitemap, statuses: dict[str, int | None]) -> Check:
    broken = [u for u, s in statuses.items() if s in (404, 410)]
    return _url_check(
        "sitemap_broken_urls", "No broken URLs in the sitemap", sitemap, broken, len(statuses), "return 404"
    )


def redirects_check(sitemap: Sitemap, statuses: dict[str, int | None]) -> Check:
    moved = [u for u, s in statuses.items() if s is not None and 300 <= s < 400]
    return _url_check(
        "sitemap_redirects", "No redirects in the sitemap", sitemap, moved, len(statuses), "redirect"
    )


def freshness_check(sitemap: Sitemap, now: datetime) -> Check:
    label = "Sitemap kept up to date"
    if not sitemap.found:
        return Check("sitemap_fresh", label, UNKNOWN, "Skipped: no sitemap.")
    if not sitemap.lastmods:
        return Check("sitemap_fresh", label, UNKNOWN, "The sitemap has no <lastmod> dates.")
    newest = max(sitemap.lastmods)
    age = (now - newest).days
    evidence = {"newest_lastmod": newest.date().isoformat(), "age_days": age}
    if age > STALE_AFTER_DAYS:
        return Check("sitemap_fresh", label, FAILED, f"Newest update is {age} days old.", evidence)
    return Check("sitemap_fresh", label, PASSED, f"Updated {age} days ago.", evidence)


def _parse_date(text: str) -> datetime | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_sitemap(xml_text: str) -> tuple[list[str], list[str], list[datetime]]:
    """(page URLs, child sitemap URLs, lastmod dates) from one sitemap document. Pure."""
    try:
        root = xml_safe.fromstring(xml_text)  # refuses DTDs / entity expansion: the XML is the customer's
    except (ET.ParseError, xml_safe.UnsafeXMLError):
        return [], [], []
    pages, children, dates = [], [], []
    is_index = root.tag.endswith("sitemapindex")
    for entry in root:
        loc = (entry.findtext(f"{_NS}loc") or entry.findtext("loc") or "").strip()
        lastmod = _parse_date(entry.findtext(f"{_NS}lastmod") or entry.findtext("lastmod") or "")
        if loc:
            (children if is_index else pages).append(loc)
        if lastmod:
            dates.append(lastmod)
    return pages, children, dates


def evaluate(
    robots: tuple[str | None, int | None], sitemap: Sitemap, statuses: dict, now: datetime
) -> list[Check]:
    """All checks, in the order they're shown. Pure."""
    return [
        robots_check(*robots),
        sitemap_found_check(sitemap),
        broken_urls_check(sitemap, statuses),
        redirects_check(sitemap, statuses),
        freshness_check(sitemap, now),
    ]


# ── Fetching ────────────────────────────────────────────────────────────────


def _get(session: requests.Session, url: str) -> requests.Response | None:
    try:
        return session.get(url, timeout=TIMEOUT_SECONDS)
    except (requests.RequestException, SSRFValidationError):
        return None


def _fetch_robots(session: requests.Session, base: str) -> tuple[str | None, int | None]:
    resp = _get(session, urljoin(base, "/robots.txt"))
    if resp is None:
        return None, None
    return (resp.text if resp.ok else ""), resp.status_code


def _fetch_sitemap(session: requests.Session, base: str, robots_txt: str | None) -> Sitemap:
    candidates = robots_rules.sitemaps(robots_txt) or [urljoin(base, "/sitemap.xml")]
    reached_any = False
    for url in candidates[:2]:
        resp = _get(session, url)
        if resp is None:
            continue
        reached_any = True
        if not resp.ok:
            continue
        pages, children, dates = parse_sitemap(resp.text)
        for child in children[:MAX_CHILD_SITEMAPS]:
            child_resp = _get(session, child)
            if child_resp is not None and child_resp.ok:
                more_pages, _, more_dates = parse_sitemap(child_resp.text)
                pages += more_pages
                dates += more_dates
        if pages or children:
            return Sitemap(True, url, pages, dates)
    return Sitemap(False if reached_any else None)


def _probe(session: requests.Session, url: str) -> int | None:
    """The URL's own status, without following redirects."""
    try:
        resp = session.head(url, timeout=TIMEOUT_SECONDS, allow_redirects=False)
        if resp.status_code in (405, 501):  # HEAD not supported: ask again with GET
            resp = session.get(url, timeout=TIMEOUT_SECONDS, allow_redirects=False, stream=True)
            resp.close()
        return resp.status_code
    except (requests.RequestException, SSRFValidationError):
        return None


def run_checks(site_url: str, now: datetime | None = None) -> list[Check]:
    """Fetch robots.txt and the sitemap for ``site_url`` and run every check. Network."""
    # Redirects are followed for robots.txt and the sitemap (http -> https is normal);
    # URL probes ask for each URL's own status with allow_redirects=False.
    session = guarded_session()
    base = site_url if site_url.endswith("/") else f"{site_url}/"
    robots = _fetch_robots(session, base)
    sitemap = _fetch_sitemap(session, base, robots[0])
    sample = sitemap.urls[:URL_SAMPLE]
    statuses = {u: s for u in sample if (s := _probe(session, u)) is not None}
    return evaluate(robots, sitemap, statuses, now or datetime.now(UTC))
