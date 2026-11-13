"""The indexing report a brand owner sees: latest Search Console check vs the one before.

Reads ``GSCIndexStatusSnapshot`` rows written by the index sync; it never calls
Google itself. Counts are Google's own verdicts per page, so "indexed" here
means Google says the page is on Google, nothing weaker.
"""

from __future__ import annotations

from apps.integrations.models import GSCIndexStatusSnapshot, Integration

from . import bing, requests
from .reasons import classify

SAMPLE_URLS = 5
CHANGE_URLS = 10


def search_console(organization) -> Integration | None:
    """The brand's active Search Console connection, if any."""
    return Integration.objects.filter(
        organization=organization, provider=Integration.Provider.GOOGLE_SEARCH_CONSOLE, is_active=True
    ).first()


def _complete(integration: Integration) -> list[GSCIndexStatusSnapshot]:
    return list(integration.gsc_index_snapshots.filter(sync_status="complete").order_by("-created_at")[:2])


def _reasons(pages: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for page in pages:
        if page.get("on_google"):
            continue
        reason = classify(page.get("coverage_state", ""))
        group = groups.setdefault(
            reason.key,
            {
                "key": reason.key,
                "label": reason.label,
                "explanation": reason.explanation,
                "fixable": reason.fixable,
                "count": 0,
                "urls": [],
            },
        )
        group["count"] += 1
        if len(group["urls"]) < SAMPLE_URLS:
            group["urls"].append(page.get("url", ""))
    return sorted(groups.values(), key=lambda g: (-g["count"], g["label"]))


def _indexed_urls(snapshot: GSCIndexStatusSnapshot) -> set[str]:
    return {p.get("url", "") for p in snapshot.pages if p.get("on_google")}


def _changes(latest: GSCIndexStatusSnapshot, previous: GSCIndexStatusSnapshot | None) -> dict:
    if previous is None:
        return {"indexed_change": None, "newly_indexed": [], "dropped": []}
    now, before = _indexed_urls(latest), _indexed_urls(previous)
    checked_both = {p.get("url", "") for p in latest.pages} & {p.get("url", "") for p in previous.pages}
    return {
        "indexed_change": latest.indexed_count - previous.indexed_count,
        "newly_indexed": sorted(now - before)[:CHANGE_URLS],
        "dropped": sorted((before - now) & checked_both)[:CHANGE_URLS],
    }


def _status(integration: Integration | None, latest: GSCIndexStatusSnapshot | None) -> str:
    if integration is None:
        return "not_connected"
    if not integration.metadata.get("site_url"):
        return "no_property"
    newest = integration.gsc_index_snapshots.order_by("-created_at").first()
    if newest is not None and newest.sync_status in ("pending", "syncing"):
        return "checking"
    if latest is None:
        return "failed" if newest is not None else "never_checked"
    return "ready"


def build(organization) -> dict:
    """Everything the Indexing tab shows, from stored snapshots only."""
    integration = search_console(organization)
    snapshots = _complete(integration) if integration else []
    latest = snapshots[0] if snapshots else None
    program = getattr(organization, "growth_program", None)
    report = {
        "bing": bing.state(program) if program else None,
        "status": _status(integration, latest),
        "property": integration.metadata.get("site_url", "") if integration else "",
        "checked_at": None,
        "checked": 0,
        "indexed": 0,
        "not_indexed": 0,
        "reasons": [],
        "indexed_change": None,
        "newly_indexed": [],
        "dropped": [],
        "to_request": [],
        "requested": [],
    }
    if latest is None:
        return report
    previous = snapshots[1] if len(snapshots) > 1 else None
    return {
        **report,
        **requests.weekly_list(program, report["property"], latest.pages),
        "checked_at": latest.created_at,
        "checked": latest.checked_count,
        "indexed": latest.indexed_count,
        "not_indexed": latest.not_indexed_count,
        "reasons": _reasons(latest.pages),
        **_changes(latest, previous),
    }
