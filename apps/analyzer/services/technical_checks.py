"""
Run a site's technical checks for an analysis run, keep every result, and raise an
On-site ticket only for the ones that failed.

A failed check always gets a ticket: unlike generated recommendations it is not
dropped when the technical pillar already scores well or the per-pillar cap is
reached, because a blocked GPTBot matters on an otherwise healthy site. Passed and
unknown checks never become tickets, so they never enter Today or Done.

robots.txt and sitemap-found reuse the analyzer's existing finding codes
(``ai_bots_blocked``, ``no_sitemap``) so the same problem is never ticketed twice.
"""

from __future__ import annotations

import logging

from django.db import transaction

from ..models import AnalysisRun, Recommendation, TechnicalCheck
from ..pipeline import technical_checks
from ..pipeline.recommendations import RECOMMENDATION_RULES, STEP_META

logger = logging.getLogger("apps")

# Check code -> finding code of the ticket it raises when it fails.
FINDING_FOR_CHECK = {
    "robots_ai_bots": "ai_bots_blocked",
    "sitemap_found": "no_sitemap",
    "sitemap_broken_urls": "sitemap_broken_urls",
    "sitemap_redirects": "sitemap_redirects",
    "sitemap_fresh": "sitemap_stale",
}

NEW_TICKETS = {
    "sitemap_broken_urls": {
        "pillar": "technical",
        "priority": "high",
        "title": "Fix broken URLs in your sitemap",
        "description": "Your sitemap lists pages that return 404. AI crawlers waste their visit on dead pages "
        "and may trust the sitemap less.",
        "action": "STEP 1: Open the pages listed below.\nSTEP 2: Restore each page, or remove it from the sitemap.\n"
        "STEP 3: Regenerate the sitemap in your CMS and re-run the analysis.",
        "category": "technical",
    },
    "sitemap_redirects": {
        "pillar": "technical",
        "priority": "medium",
        "title": "List final URLs in your sitemap, not redirects",
        "description": "Your sitemap lists URLs that redirect. Crawlers expect the final address; redirects "
        "slow discovery.",
        "action": "STEP 1: Open each URL below and note where it redirects.\nSTEP 2: Replace it in the sitemap "
        "with the final URL.\nSTEP 3: Regenerate the sitemap and re-run the analysis.",
        "category": "technical",
    },
    "sitemap_stale": {
        "pillar": "technical",
        "priority": "low",
        "title": "Keep your sitemap up to date",
        "description": "Your sitemap's newest <lastmod> date is over 90 days old, so crawlers may not "
        "revisit your recent pages.",
        "action": "STEP 1: Turn on automatic sitemap updates in your CMS (most do this by default).\n"
        "STEP 2: Make sure <lastmod> changes when a page changes.\nSTEP 3: Re-run the analysis.",
        "category": "technical",
    },
}
WHY = "AI engines can only cite pages their crawlers can reach and find."
DEFAULT_MINUTES = 15


def _ticket_fields(finding: str, check: technical_checks.Check) -> dict:
    rule = RECOMMENDATION_RULES.get(finding) or NEW_TICKETS[finding]
    meta = STEP_META.get(finding, {})
    return {
        "pillar": rule["pillar"],
        "priority": rule["priority"],
        "title": rule["title"],
        "description": f"{rule['description']} {check.detail}".strip(),
        "action": rule["action"],
        "category": rule.get("category", "technical"),
        "finding_code": finding,
        "why": WHY,
        "steps": meta.get("steps", []),
        "xp_reward": meta.get("xp_reward", 20),
        "difficulty": meta.get("difficulty", "easy"),
        "estimated_minutes": meta.get("estimated_minutes", DEFAULT_MINUTES),
        "evidence": {"technical_check": check.code, **check.evidence},
        "affected_pages": check.evidence.get("examples", []),
    }


def _raise_ticket(run: AnalysisRun, check: technical_checks.Check) -> None:
    """One ticket per failed check; an existing one for the same finding gets the fresh evidence."""
    finding = FINDING_FOR_CHECK[check.code]
    fields = _ticket_fields(finding, check)
    existing = run.recommendations.filter(finding_code=finding).first()
    if existing:
        existing.evidence = fields["evidence"]
        existing.affected_pages = fields["affected_pages"]
        existing.save(update_fields=["evidence", "affected_pages"])
        return
    Recommendation.objects.create(analysis_run=run, **fields)


def store(run: AnalysisRun, checks: list[technical_checks.Check]) -> None:
    """Save every result for the run and ticket the failures. Idempotent per run."""
    with transaction.atomic():
        for check in checks:
            TechnicalCheck.objects.update_or_create(
                analysis_run=run,
                code=check.code,
                defaults={
                    "label": check.label,
                    "status": check.status,
                    "detail": check.detail[:300],
                    "evidence": check.evidence,
                },
            )
            if check.status == TechnicalCheck.Status.FAILED:
                _raise_ticket(run, check)


def run_for(run: AnalysisRun) -> None:
    """Fetch and check ``run.url``, then store. Never fails the analysis."""
    try:
        store(run, technical_checks.run_checks(run.url))
    except Exception:  # noqa: BLE001 - checks are an addition; an error must not fail the analysis
        logger.exception("technical checks failed for run %s", run.pk)


def latest_for_org(organization_id: int) -> tuple[AnalysisRun | None, list[TechnicalCheck]]:
    """The newest run of the brand that has check results, and those results."""
    run = (
        AnalysisRun.objects.filter(organization_id=organization_id, technical_checks__isnull=False)
        .order_by("-created_at")
        .distinct()
        .first()
    )
    return run, list(run.technical_checks.all()) if run else []
