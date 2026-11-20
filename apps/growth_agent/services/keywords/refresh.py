"""
Weekly keyword read for a brand, from signalor.ai's DataForSEO account.

When the brand connected its own Semrush or Ahrefs key (+ panel), every read
uses that account. Otherwise brand keywords reuse the analyzer's 7-day Domain
Analytics snapshot when it is fresh (no extra cost), else one Ranked Keywords call. Competitor gaps cost
one call per competitor (top 3). Runs on the worker, at most once a day on
request and once a week on schedule.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.integrations.services.dataforseo import (
    DataForSEOError,
    DataForSEONotConfigured,
    fetch_ranked_keywords,
)

from ...models import AgentActionLog, GrowthProgram, KeywordSnapshot, PositioningReport
from .. import run_guard
from ..execution import audit
from ..execution.brand import display_name
from ..positioning.inputs import bare_domain
from . import accounts, google_source, providers
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


Fetch = Callable[[str], list[dict]]


def _dataforseo(domain: str) -> list[dict]:
    return fetch_ranked_keywords(domain, limit=KEYWORD_LIMIT)


def _reader(organization) -> tuple[Fetch, str]:
    """The brand's own Semrush or Ahrefs account when connected, else Signalor's DataForSEO."""
    own = accounts.own_source(organization)
    if own is None:
        return _dataforseo, "dataforseo"
    provider, key = own
    return (lambda domain: providers.FETCHERS[provider](domain, key)), provider


def _brand_keywords(run, domain: str, fetch: Fetch, source: str) -> tuple[list[dict], str]:
    """(rows, source). With the built-in data, the analyzer's fresh 7-day snapshot saves a call."""
    snapshot = getattr(run, "domain_analytics", None) if run else None
    fresh = snapshot and snapshot.top_keywords and timezone.now() - snapshot.synced_at < SNAPSHOT_FRESH
    if source == "dataforseo" and fresh:
        return list(snapshot.top_keywords), "analyzer_snapshot"
    return fetch(domain), source


def _is_billing(exc: DataForSEOError) -> bool:
    """DataForSEO answers 402 when signalor.ai's account is out of credits."""
    return "402" in str(exc)


def _rival_keywords(rivals: list[rules.Rival], fetch: Fetch) -> dict[rules.Rival, list[dict]]:
    """One call per competitor. A competitor that fails is left out, not the whole read."""
    out = {}
    for rival in rivals:
        try:
            out[rival] = fetch(rival.domain)
        except DataForSEOError as exc:
            if _is_billing(exc):
                raise  # every other call would fail too; don't store an empty "no gaps" read
            logger.warning("keywords: competitor %s skipped: %s", rival.domain, exc)
        except providers.ProviderError:
            logger.warning("keywords: competitor %s skipped (own keyword account)", rival.domain)
    return out


def _previous_gaps(program: GrowthProgram) -> set[str]:
    last = program.keyword_snapshots.filter(kind=KeywordSnapshot.Kind.GAPS).first()
    return {row.get("keyword", "") for row in last.rows} if last else set()


GOLDEN = "golden_keywords_found"
RANK_CHANGES = "keyword_rank_changes"


def _log_findings(program: GrowthProgram, gap_rows: list[dict], last_brand, brand_rows: list[dict]) -> None:
    """Activity lines worth the owner's attention: golden keywords, and ranking moves since last week."""
    found = rules.golden(gap_rows, first_read=last_brand is None)
    if found:
        audit.record(program, None, GOLDEN, AgentActionLog.Outcome.SUCCEEDED, {"keywords": found})
    if last_brand is None:
        return
    changes = rules.rank_changes(last_brand.rows, brand_rows)
    if any(changes.values()):
        audit.record(program, None, RANK_CHANGES, AgentActionLog.Outcome.SUCCEEDED, changes)


def _from_google(program: GrowthProgram) -> bool:
    """No keyword provider: use the weekly rank check's Google positions instead."""
    from ..rank_tracking.check import competitors  # noqa: PLC0415  (rank_tracking imports this module)

    players = [p for p in competitors(program) if p.domain]
    if not google_source.store(program, players):
        _fail(program, "not_configured")
        return False
    detail = {"source": google_source.SOURCE, "competitors": [p.domain for p in players[1:]]}
    audit.record(program, None, DONE, AgentActionLog.Outcome.SUCCEEDED, detail)
    return True


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
    fetch, source = _reader(org)
    try:
        brand_rows, source = _brand_keywords(run, domain, fetch, source)
        rival_rows = _rival_keywords(rivals, fetch)
    except DataForSEONotConfigured:
        return _from_google(program)
    except providers.ProviderError:
        # The brand's own Semrush or Ahrefs key was refused or out of units.
        _fail(program, "own_account_error")
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
    last_brand = program.keyword_snapshots.filter(kind=KeywordSnapshot.Kind.BRAND).first()
    with transaction.atomic():
        _log_findings(program, gap_rows, last_brand, rules.brand_rows(brand_rows))
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

    if not run_guard.may_start(program, STARTED, DONE, guard):
        return False
    audit.record(program, None, STARTED, AgentActionLog.Outcome.SUCCEEDED, {}, actor_email=actor_email)
    transaction.on_commit(lambda: dispatch_keyword_refresh(program.id))
    return True


def weekly(program: GrowthProgram) -> bool:
    return start(program, guard=REFRESH_EVERY)
