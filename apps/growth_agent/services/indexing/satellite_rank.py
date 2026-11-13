"""Weekly Google check of each satellite article: indexed yet, position, clicks.

The 5 satellite sites belong to signalor.ai, so they're read through
signalor.ai's own Search Console connection (the Google account behind
``settings.SATELLITE_GSC_ORG_SLUG``), not the client's. Each satellite domain
must be a property in that account; articles on a site that isn't are
reported as "site not in Search Console".

Per published article (task with tactic ``satellite_article``, status done):
- URL Inspection: is the page on Google, and Google's reason if not;
- Search analytics: clicks, impressions and average position, last 30 days.

The results go to the Agent log (``satellite_rank_checked``), one entry per
article per week. Flags for the owner come from those results:
- not on Google 7 days after publishing  -> "needs_links";
- on Google but below page 1 after 14 days -> "needs_refresh".
"""

from __future__ import annotations

import logging
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.integrations.models import Integration
from apps.organizations.models import Organization

from ...models import AgentActionLog, GrowthProgram, GrowthTask
from ..execution import audit
from . import report
from .reasons import classify

logger = logging.getLogger("apps")
CHECKED, STARTED = "satellite_rank_checked", "satellite_rank_started"
CHECK_EVERY = timedelta(days=7)
START_GUARD = timedelta(hours=6)
LINKS_AFTER = timedelta(days=7)
REFRESH_AFTER = timedelta(days=14)
PAGE_ONE = 10.0


def ops_search_console() -> Integration | None:
    """signalor.ai's own Search Console connection, used for the satellite sites."""
    slug = getattr(settings, "SATELLITE_GSC_ORG_SLUG", "")
    org = Organization.objects.filter(slug=slug).first() if slug else None
    return report.search_console(org) if org else None


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def property_for(properties: list[str], page_url: str) -> str:
    """The account's property covering the page: a domain property, else a URL prefix."""
    host = _host(page_url)
    if f"sc-domain:{host}" in properties:
        return f"sc-domain:{host}"
    return next((p for p in properties if p.startswith("http") and page_url.startswith(p.rstrip("/"))), "")


def published(program: GrowthProgram) -> list[tuple[GrowthTask, object]]:
    """Published satellite articles with when they went live."""
    tasks = GrowthTask.objects.filter(
        cycle__program=program, tactic="satellite_article", status=GrowthTask.Status.DONE
    ).exclude(evidence_url="")
    logs = AgentActionLog.objects.filter(program=program, task__in=tasks, action="publish")
    live_at = {log.task_id: log.created_at for log in logs.order_by("created_at")}
    return [(task, live_at.get(task.id, task.updated_at)) for task in tasks]


def _latest_checks(program: GrowthProgram) -> dict[int, AgentActionLog]:
    entries = AgentActionLog.objects.filter(program=program, action=CHECKED).order_by("created_at")
    return {entry.task_id: entry for entry in entries}


def check_one(integration: Integration, properties: list[str], url: str) -> dict:
    """Google's view of one article. Network: URL Inspection and Search analytics."""
    from apps.integrations.services.gsc import fetch_gsc_page_metrics, inspect_gsc_url

    site = property_for(properties, url)
    if not site:
        return {"url": url, "property_found": False}
    inspection = inspect_gsc_url(integration, url, site_url=site)
    metrics = fetch_gsc_page_metrics(integration, url, site_url=site)
    return {
        "url": url,
        "property_found": True,
        "indexed": bool(inspection["on_google"]),
        "reason": "" if inspection["on_google"] else classify(inspection["coverage_state"]).label,
        "position": metrics["position"],
        "clicks": metrics["clicks"],
        "impressions": metrics["impressions"],
    }


def run_checks(program: GrowthProgram) -> int:
    """Check every published article that wasn't checked this week. Runs on the worker."""
    from apps.integrations.services.gsc import list_gsc_sites

    integration = ops_search_console()
    if integration is None:
        return 0
    properties = [s["site_url"] for s in list_gsc_sites(integration)]
    latest, now, done = _latest_checks(program), timezone.now(), 0
    for task, _ in published(program):
        last = latest.get(task.id)
        if last is not None and now - last.created_at < CHECK_EVERY:
            continue
        try:
            detail = check_one(integration, properties, task.evidence_url)
        except Exception as exc:  # noqa: BLE001 - one article failing must not stop the others
            logger.warning("satellite rank check failed for task %s: %s", task.id, exc)
            detail = {"url": task.evidence_url, "error": str(exc)[:300]}
        audit.record(program, task, CHECKED, AgentActionLog.Outcome.SUCCEEDED, detail)
        done += 1
    return done


def _due(program: GrowthProgram) -> bool:
    latest, now = _latest_checks(program), timezone.now()
    return any(
        task.id not in latest or now - latest[task.id].created_at >= CHECK_EVERY
        for task, _ in published(program)
    )


def weekly(program: GrowthProgram) -> bool:
    """Queue this week's checks when an article is due and Search Console is set up."""
    from ...analysis_tasks import dispatch_satellite_rank

    if ops_search_console() is None or not _due(program):
        return False
    last = AgentActionLog.objects.filter(program=program, action=STARTED).order_by("-created_at").first()
    if last is not None and timezone.now() - last.created_at < START_GUARD:
        return False
    audit.record(program, None, STARTED, AgentActionLog.Outcome.SUCCEEDED, {})
    transaction.on_commit(lambda: dispatch_satellite_rank(program.id))
    return True


def flag(check: dict | None, live_for: timedelta) -> str:
    if not check or not check.get("property_found", True) or check.get("error"):
        return ""
    if not check.get("indexed"):
        return "needs_links" if live_for >= LINKS_AFTER else ""
    position = check.get("position") or 0
    return "needs_refresh" if live_for >= REFRESH_AFTER and (position == 0 or position > PAGE_ONE) else ""


def articles(program: GrowthProgram) -> dict:
    """What the Indexing tab shows for satellite articles. No network calls."""
    latest, now = _latest_checks(program), timezone.now()
    rows = []
    for task, live_at in published(program):
        entry = latest.get(task.id)
        check = entry.detail if entry else None
        rows.append(
            {
                "task_id": task.id,
                "topic": task.target,
                "url": task.evidence_url,
                "published_at": live_at,
                "checked_at": entry.created_at if entry else None,
                "check": check,
                "flag": flag(check, now - live_at),
            }
        )
    rows.sort(key=lambda r: r["published_at"], reverse=True)
    return {"search_console": ops_search_console() is not None, "articles": rows}
