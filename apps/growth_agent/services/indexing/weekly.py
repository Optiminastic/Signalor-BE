"""The weekly indexing round for one brand, run from ``run_growth_tasks``.

- Start a Search Console check when the newest one is a week old (or none).
- When a check has finished, log it once in the Agent log with its numbers.
Checks run on the worker queue; nothing here calls Google directly.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ...models import AgentActionLog, GrowthProgram
from ..execution import audit
from . import report

CHECK_EVERY = timedelta(days=7)
# A check inspects every sitemap page (about 8 seconds each); don't start another meanwhile.
RESTART_GUARD = timedelta(hours=6)
STARTED = "indexing_check_started"
CHECKED = "indexing_checked"


def _last_started(program: GrowthProgram):
    entry = AgentActionLog.objects.filter(program=program, action=STARTED).order_by("-created_at").first()
    return entry.created_at if entry else None


def start_check(program: GrowthProgram, actor_email: str = "") -> bool:
    """Queue a Search Console check now. False when not connected or one started recently."""
    from ...analysis_tasks import dispatch_indexing_check

    integration = report.search_console(program.organization)
    if integration is None or not integration.metadata.get("site_url"):
        return False
    last = _last_started(program)
    if last is not None and timezone.now() - last < RESTART_GUARD:
        return False
    detail = {"property": integration.metadata["site_url"]}
    audit.record(program, None, STARTED, AgentActionLog.Outcome.SUCCEEDED, detail, actor_email=actor_email)
    transaction.on_commit(lambda: dispatch_indexing_check(integration.id))
    return True


def _due(integration) -> bool:
    newest = integration.gsc_index_snapshots.order_by("-created_at").first()
    return newest is None or timezone.now() - newest.created_at >= CHECK_EVERY


def log_finished(program: GrowthProgram) -> bool:
    """Write one Agent log entry for the newest finished check, if not written yet."""
    built = report.build(program.organization)
    integration = report.search_console(program.organization)
    latest = integration.gsc_index_snapshots.filter(sync_status="complete").first() if integration else None
    if latest is None:
        return False
    if AgentActionLog.objects.filter(program=program, action=CHECKED, detail__snapshot_id=latest.id).exists():
        return False
    detail = {
        "snapshot_id": latest.id,
        "checked": built["checked"],
        "indexed": built["indexed"],
        "not_indexed": built["not_indexed"],
        "indexed_change": built["indexed_change"],
        "top_reason": built["reasons"][0]["label"] if built["reasons"] else "",
        "to_request": len(built["to_request"]),
    }
    audit.record(program, None, CHECKED, AgentActionLog.Outcome.SUCCEEDED, detail)
    return True


def weekly_round(program: GrowthProgram) -> None:
    integration = report.search_console(program.organization)
    if integration is None:
        return
    log_finished(program)
    if _due(integration):
        start_check(program)
