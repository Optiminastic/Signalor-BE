"""
Weekly keyword read for a brand, from signalor.ai's DataForSEO account.

Brand keywords reuse the analyzer's 7-day Domain Analytics snapshot when it is
fresh (no extra cost); otherwise one Ranked Keywords call. Competitor gaps cost
one call per competitor (top 3). Runs on the worker, at most once a day on
request and once a week on schedule.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.integrations.services.dataforseo import (
    DataForSEOError,
    DataForSEONotConfigured,
    fetch_ranked_keywords,
)

from ...models import AgentActionLog, GrowthProgram, KeywordSnapshot, PositioningReport
from ..execution import audit
from ..execution.brand import display_name
from ..positioning.inputs import bare_domain
from . import gaps as rules

logger = logging.getLogger("apps")

STARTED = "keywords_refresh_started"
DONE = "keywords_refreshed"
START_GUARD = timedelta(hours=24)
REFRESH_EVERY = timedelta(days=6)
SNAPSHOT_FRESH = timedelta(days=7)
KEYWORD_LIMIT = 100


def latest_run(program: GrowthProgram):
    report = (
        program.positioning_reports.filter(
            status=PositioningReport.Status.COMPLETE, analysis_run__isnull=False
        )
        .select_related("analysis_run")
        .first()
    )
    return (report.analysis_run, report) if report else (None, None)


def _brand_keywords(run, domain: str) -> tuple[list[dict], str]:
    """(rows, source). The analyzer's cached snapshot when fresh, else one DataForSEO call."""
    snapshot = getattr(run, "domain_analytics", None) if run else None
    if snapshot and snapshot.top_keywords and timezone.now() - snapshot.synced_at < SNAPSHOT_FRESH:
        return list(snapshot.top_keywords), "analyzer_snapshot"
    return fetch_ranked_keywords(domain, limit=KEYWORD_LIMIT), "dataforseo"


def _is_billing(exc: DataForSEOError) -> bool:
    """DataForSEO answers 402 when signalor.ai's account is out of credits."""
    return "402" in str(exc)


def _rival_keywords(rivals: list[rules.Rival]) -> dict[rules.Rival, list[dict]]:
    """One call per competitor. A competitor that fails is left out, not the whole read."""
    out = {}
    for rival in rivals:
        try:
            out[rival] = fetch_ranked_keywords(rival.domain, limit=KEYWORD_LIMIT)
        except DataForSEOError as exc:
            if _is_billing(exc):
                raise  # every other call would fail too; don't store an empty "no gaps" read
            logger.warning("keywords: competitor %s skipped: %s", rival.domain, exc)
    return out


def _previous_gaps(program: GrowthProgram) -> set[str]:
    last = program.keyword_snapshots.filter(kind=KeywordSnapshot.Kind.GAPS).first()
    return {row.get("keyword", "") for row in last.rows} if last else set()


def _fail(program: GrowthProgram, reason: str) -> None:
    audit.record(program, None, DONE, AgentActionLog.Outcome.FAILED, {"reason": reason})


def run_refresh(program: GrowthProgram) -> bool:
    """Read and store this week's keywords. Returns True when stored. Runs on the worker."""
    org = program.organization
    domain = bare_domain(org.url)
    run, report = latest_run(program)
    if not domain or run is None:
        _fail(program, "no_analysis")
        return False
    brand = rules.Rival(display_name(org), domain)
    candidates = [rules.Rival(c.name, bare_domain(c.url)) for c in run.competitors.all().order_by("id")]
    rivals = rules.top_rivals(candidates, report.lost_prompts, own_domain=domain)
    try:
        brand_rows, source = _brand_keywords(run, domain)
        rival_rows = _rival_keywords(rivals)
    except DataForSEONotConfigured:
        _fail(program, "not_configured")
        return False
    except DataForSEOError as exc:
        if _is_billing(exc):
            # signalor.ai's own DataForSEO account needs topping up: an ops problem, not the brand's.
            logger.error("keywords: DataForSEO account has no credits (program %s)", program.pk)
        else:
            logger.warning("keywords: brand read failed for program %s: %s", program.pk, exc)
        _fail(program, "provider_error")
        return False
    gap_rows = rules.gaps(brand_rows, rival_rows, brand=brand, previous=_previous_gaps(program))
    used = [{"name": r.name, "domain": r.domain} for r in rival_rows]
    with transaction.atomic():
        KeywordSnapshot.objects.create(
            program=program,
            kind=KeywordSnapshot.Kind.BRAND,
            rows=rules.brand_rows(brand_rows),
            meta={"source": source},
        )
        KeywordSnapshot.objects.create(
            program=program, kind=KeywordSnapshot.Kind.GAPS, rows=gap_rows, meta={"competitors": used}
        )
        detail = {
            "keywords": len(brand_rows),
            "gaps": len(gap_rows),
            "competitors": [r["domain"] for r in used],
        }
        audit.record(program, None, DONE, AgentActionLog.Outcome.SUCCEEDED, detail)
    return True


def start(program: GrowthProgram, *, guard: timedelta, actor_email: str = "") -> bool:
    """Queue a refresh, at most once per ``guard`` (each refresh is paid)."""
    from ...analysis_tasks import dispatch_keyword_refresh

    last = AgentActionLog.objects.filter(program=program, action=STARTED).order_by("-created_at").first()
    if last is not None and timezone.now() - last.created_at < guard:
        return False
    audit.record(program, None, STARTED, AgentActionLog.Outcome.SUCCEEDED, {}, actor_email=actor_email)
    transaction.on_commit(lambda: dispatch_keyword_refresh(program.id))
    return True


def weekly(program: GrowthProgram) -> bool:
    return start(program, guard=REFRESH_EVERY)
