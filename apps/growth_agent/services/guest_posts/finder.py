"""
Find sites that accept guest articles on the brand's topic, and put them in
this month's plan as guest-post tasks.

Network: Serper web search (``SERPER_API_KEY``) and fetching each candidate's
guidelines page through the SSRF guard. Runs on the worker, weekly and when
the owner asks. At most ``MONTHLY_CAP`` guest-post tasks per plan, matching the
monthly cap on new guest pitches.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.analyzer.url_guard import SSRFValidationError, guarded_session

from ...models import AgentActionLog, GrowthCycle, GrowthProgram, GrowthTask, OutreachContact
from ..execution import audit
from ..planning.plan import _report_payload, active_cycle
from ..planning.schedule import DAYS_PER_WEEK, WEEKS_PER_CYCLE
from ..planning.tactics import TACTICS
from . import candidates as rules

logger = logging.getLogger("apps")

TACTIC = "guest_post"
MONTHLY_CAP = 8
DAYS_BETWEEN = 3
SERPER_ENDPOINT = "https://google.serper.dev/search"
SEARCH_TIMEOUT = 12
RESULTS_PER_QUERY = 10
FETCH_TIMEOUT = 10
MAX_PAGE_BYTES = 1_500_000
MAX_PAGES_CHECKED = 25
GUIDELINE_PATHS = ("/write-for-us", "/contribute", "/guest-post", "/write-for-us/")
USER_AGENT = "Mozilla/5.0 (compatible; SignalorGrowthAgent/1.0)"
CITED_SCORE = 0.9
SEARCH_SCORE = 0.6
STARTED = "guest_find_started"
FOUND = "guest_sites_found"
START_GUARD = timedelta(hours=6)
FIND_EVERY = timedelta(days=6)


@dataclass(frozen=True)
class Found:
    domain: str
    guidelines_url: str
    source: str  # "cited" (AI answers cite it) or "search"
    topic: str


def search(query: str) -> list[str]:
    """Organic result URLs for one query; [] without a key or on any failure."""
    key = os.getenv("SERPER_API_KEY", "")
    if not key:
        return []
    try:
        reply = requests.post(
            SERPER_ENDPOINT,
            json={"q": query, "num": RESULTS_PER_QUERY},
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            timeout=SEARCH_TIMEOUT,
        )
        reply.raise_for_status()
    except requests.RequestException as exc:
        logger.info("guest-site search failed: %s", type(exc).__name__)
        return []
    return [r["link"] for r in reply.json().get("organic") or [] if r.get("link")]


def fetch(session: requests.Session, url: str) -> str | None:
    try:
        reply = session.get(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT}, stream=True)
        if reply.status_code != 200 or "html" not in reply.headers.get("Content-Type", "html"):
            return None
        return reply.raw.read(MAX_PAGE_BYTES, decode_content=True).decode(reply.encoding or "utf-8", "ignore")
    except (SSRFValidationError, requests.RequestException, UnicodeError):
        return None


def _blocked_domains(program: GrowthProgram, cycle: GrowthCycle | None) -> set[str]:
    org = program.organization
    blocked = {rules.domain_of(org.url)} if org.url else set()
    blocked |= {rules.domain_of(url) for url in getattr(settings, "SATELLITE_SITES", {}).values()}
    blocked |= set(OutreachContact.objects.filter(organization=org).values_list("domain", flat=True))
    if cycle is not None:
        blocked |= {rules.domain_of(t.target_url) for t in cycle.tasks.exclude(target_url="")}
        run = getattr(cycle.positioning_report, "analysis_run", None)
        if run is not None:
            # Every competitor the site analysis found, not only those AI answers name.
            blocked |= {rules.domain_of(c.url) for c in run.competitors.all()}
    return {d.removeprefix("www.") for d in blocked if d}


def _leads(report: dict, topic_list: list[str]) -> list[tuple[str, str, str]]:
    """(url, source, topic) to check: cited sites' guidelines pages first, then search results."""
    leads = [
        (f"https://{domain}{path}", "cited", topic_list[0])
        for domain in rules.cited_domains(report)
        for path in GUIDELINE_PATHS[:2]
    ]
    for topic in topic_list:
        for query in rules.queries([topic]):
            leads += [(url, "search", topic) for url in search(query)]
    return leads


def find_sites(program: GrowthProgram, limit: int) -> list[Found]:
    cycle = active_cycle(program)
    if cycle is None or cycle.positioning_report is None or limit <= 0:
        return []
    report = _report_payload(cycle.positioning_report)
    topic_list = rules.topics(report)
    if not topic_list:
        return []
    blocked, rivals = _blocked_domains(program, cycle), rules.rival_names(report)
    found: list[Found] = []
    checked, session = 0, guarded_session()
    for url, source, topic in _leads(report, topic_list):
        domain = rules.domain_of(url)
        if len(found) >= limit or checked >= MAX_PAGES_CHECKED:
            break
        if rules.excluded(domain, blocked_domains=blocked, rivals=rivals):
            continue
        page, checked = _guidelines_page(session, url, checked)
        if page and rules.check_page(page[1], topic_list).ok:
            found.append(Found(domain=domain, guidelines_url=page[0], source=source, topic=topic))
            blocked.add(domain)
    return found


def _guidelines_page(session, url: str, checked: int) -> tuple[tuple[str, str] | None, int]:
    """((guidelines url, html), pages fetched so far). A page whose address isn't a guidelines
    page (a blog post with a "Write for us" footer link) only counts through that link."""
    html = fetch(session, url)
    checked += 1
    if not html:
        return None, checked
    if rules.is_guidelines_url(url):
        return (url, html), checked
    link = rules.guidelines_link(html, url)
    if not link or checked >= MAX_PAGES_CHECKED:
        return None, checked
    linked = fetch(session, link)
    return ((link, linked) if linked else None), checked + 1


def _task(cycle: GrowthCycle, site: Found, day: date) -> GrowthTask:
    tactic = TACTICS[TACTIC]
    why = (
        "AI answers already cite this site, and it invites guest articles."
        if site.source == "cited"
        else f"It invites guest articles on {site.topic}."
    )
    week = min((day - cycle.starts_on).days // DAYS_PER_WEEK + 1, WEEKS_PER_CYCLE)
    return GrowthTask(
        cycle=cycle,
        week=max(week, 1),
        scheduled_for=day,
        channel=tactic.channel,
        tactic=TACTIC,
        title=tactic.title.format(target=site.domain)[:300],
        rationale=why,
        target=site.domain,
        target_url=site.guidelines_url,
        prompts=[site.topic],
        impact_score=CITED_SCORE if site.source == "cited" else SEARCH_SCORE,
        effort=tactic.effort,
        draft_payload={
            "guest": {"guidelines_url": site.guidelines_url, "source": site.source, "topic": site.topic}
        },
    )


def run_find(program: GrowthProgram) -> int:
    """Find sites and add them to the active plan. Returns how many were added. Runs on the worker."""
    cycle = active_cycle(program)
    room = MONTHLY_CAP - (cycle.tasks.filter(tactic=TACTIC).count() if cycle else 0)
    sites = find_sites(program, room)
    today = timezone.localdate()
    rows = [_task(cycle, site, today + timedelta(days=i * DAYS_BETWEEN)) for i, site in enumerate(sites)]
    with transaction.atomic():
        GrowthTask.objects.bulk_create(rows)
        detail = {"count": len(rows), "domains": [r.target for r in rows], "room": max(room, 0)}
        audit.record(program, None, FOUND, AgentActionLog.Outcome.SUCCEEDED, detail)
    return len(rows)


def start(program: GrowthProgram, *, guard: timedelta, actor_email: str = "") -> bool:
    """Queue a search, at most once per ``guard``."""
    from ...analysis_tasks import dispatch_guest_site_find

    last = AgentActionLog.objects.filter(program=program, action=STARTED).order_by("-created_at").first()
    if last is not None and timezone.now() - last.created_at < guard:
        return False
    audit.record(program, None, STARTED, AgentActionLog.Outcome.SUCCEEDED, {}, actor_email=actor_email)
    transaction.on_commit(lambda: dispatch_guest_site_find(program.id))
    return True


def weekly(program: GrowthProgram) -> bool:
    if active_cycle(program) is None:
        return False
    return start(program, guard=FIND_EVERY)
