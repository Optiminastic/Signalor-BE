"""
Daily site watch for a brand: fetch the homepage, robots.txt and sitemap, and
read Search Console's stored daily clicks. Each check is logged ("site_checked",
carrying the state so tomorrow's check can compare), and each change for the
worse, or recovery, gets its own "site_anomaly" line in the Activity log.

Fetches go through the SSRF guard. Runs on the worker, at most once a day.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urljoin

import requests
from django.db import transaction
from django.utils import timezone

from apps.analyzer.url_guard import SSRFValidationError, guarded_session
from apps.integrations.models import GSCDataSnapshot, Integration

from ...models import AgentActionLog, GrowthProgram
from ..execution import audit
from . import checks

logger = logging.getLogger("apps")

STARTED = "site_check_started"
CHECKED = "site_checked"
ANOMALY = "site_anomaly"
CHECK_EVERY = timedelta(hours=20)
CLICK_ALERT_EVERY = timedelta(days=7)
TIMEOUT_SECONDS = 15
MAX_BYTES = 1_500_000
USER_AGENT = "Mozilla/5.0 (compatible; SignalorSiteWatch/1.0)"


@dataclass(frozen=True)
class Page:
    status: int
    robots_header: str
    text: str


def _get(session: requests.Session, url: str) -> Page | None:
    """One bounded GET (at most MAX_BYTES read); None when unreachable or refused by the SSRF guard."""
    try:
        reply = session.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}, stream=True)
        body = reply.raw.read(MAX_BYTES, decode_content=True).decode(reply.encoding or "utf-8", "ignore")
        return Page(reply.status_code, reply.headers.get("X-Robots-Tag", ""), body)
    except (SSRFValidationError, requests.RequestException, UnicodeError):
        return None


def _homepage(session: requests.Session, url: str) -> dict:
    reply = _get(session, url)
    if reply is None or reply.status_code >= 400:
        return {"url": url, "down": True, "status": reply.status_code if reply is not None else 0}
    page = checks.read_homepage(reply.text, reply.robots_header)
    return {"url": url, "down": False, "status": reply.status_code, **page}


def _robots_and_sitemap(session: requests.Session, url: str) -> dict:
    reply = _get(session, urljoin(url, "/robots.txt"))
    robots = reply.text if reply is not None and reply.status_code == 200 else ""
    sitemaps = checks.sitemap_urls(robots) or [urljoin(url, "/sitemap.xml")]
    found = _get(session, sitemaps[0])
    return {
        "blocked": checks.blocked_bots(robots),
        "sitemap_missing": found is None or found.status_code >= 400,
    }


def read_state(url: str) -> dict:
    session = guarded_session()
    state = _homepage(session, url)
    if not state["down"]:
        state.update(_robots_and_sitemap(session, url))
    return state


def _previous(program: GrowthProgram) -> dict | None:
    last = AgentActionLog.objects.filter(program=program, action=CHECKED).order_by("-created_at").first()
    return (last.detail or {}).get("state") if last else None


def _clicks_anomaly(program: GrowthProgram) -> checks.Anomaly | None:
    """A sharp drop in Google clicks, at most one alert a week."""
    gsc = Integration.objects.filter(
        organization=program.organization, provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE, is_active=True
    ).first()
    snapshot = (
        GSCDataSnapshot.objects.filter(integration=gsc, sync_status="complete").first() if gsc else None
    )
    drop = checks.clicks_drop(snapshot.daily_trend) if snapshot else None
    if drop is None:
        return None
    since = timezone.now() - CLICK_ALERT_EVERY
    recent = AgentActionLog.objects.filter(
        program=program, action=ANOMALY, detail__kind="clicks_drop", created_at__gte=since
    )
    return None if recent.exists() else checks.Anomaly("clicks_drop", "attention", drop)


def run_check(program: GrowthProgram) -> int:
    """Check the site and log what changed. Returns how many anomalies were logged. Worker only."""
    url = program.organization.url
    if not url:
        return 0
    previous = _previous(program)
    state = read_state(url)
    found = checks.compare(previous, state)
    clicks = _clicks_anomaly(program)
    if clicks:
        found.append(clicks)
    with transaction.atomic():
        for anomaly in found:
            outcome = (
                AgentActionLog.Outcome.SUCCEEDED
                if anomaly.severity == "recovered"
                else AgentActionLog.Outcome.BLOCKED
            )
            detail = {"kind": anomaly.kind, "severity": anomaly.severity, **anomaly.detail}
            audit.record(program, None, ANOMALY, outcome, detail)
        audit.record(
            program,
            None,
            CHECKED,
            AgentActionLog.Outcome.SUCCEEDED,
            {"state": state, "anomalies": len(found)},
        )
    return len(found)


def start(program: GrowthProgram, *, guard: timedelta = CHECK_EVERY) -> bool:
    """Queue a site check, at most once per ``guard``."""
    from ...analysis_tasks import dispatch_site_check

    last = AgentActionLog.objects.filter(program=program, action=STARTED).order_by("-created_at").first()
    if last is not None and timezone.now() - last.created_at < guard:
        return False
    audit.record(program, None, STARTED, AgentActionLog.Outcome.SUCCEEDED, {})
    transaction.on_commit(lambda: dispatch_site_check(program.id))
    return True


def daily(program: GrowthProgram) -> bool:
    return start(program)
