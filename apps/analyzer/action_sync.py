"""Materialize a run's Recommendations into UserAction tasks (idempotent).

One authoritative implementation shared by:
- ``SyncActionsView`` (dashboard-driven, on demand), and
- ``run_scheduled_analyses`` (weekly cron),
so a scheduled run updates the Tasks queue without waiting for the dashboard to sync.

Idempotent: a Recommendation already materialized into a UserAction for the run is
skipped, so it is safe to call repeatedly.

Also deduplicated **across runs**. Each analysis writes a fresh set of
Recommendation rows, while the Tasks list is organization-scoped over every run,
so materialising per-run alone surfaced one copy of each recurring finding per
analysis. A finding the user already has an open task for is not raised again.
"""

from __future__ import annotations

import logging

from .models import UserAction

logger = logging.getLogger("apps")

# Pillar → a representative ActionType for auto-materialized tasks (cosmetic; the task
# carries the recommendation's own title/description). SiteOne findings use the
# "technical" pillar, so they inherit the technical action type.
PILLAR_ACTION_TYPE = {
    "content": UserAction.ActionType.ADD_STRUCTURE,
    "schema": UserAction.ActionType.ADD_SCHEMA,
    "technical": UserAction.ActionType.ADD_SITEMAP,
    "eeat": UserAction.ActionType.ADD_ABOUT,
    "entity": UserAction.ActionType.ADD_SOCIAL,
    "ai_visibility": UserAction.ActionType.BUILD_BACKLINKS,
}

# The analyzer has 6 pillars; the Growth Agent groups them into 3 display buckets.
# One authoritative map so the frontend never hardcodes the pillar taxonomy.
PILLAR_GROUP = {
    "content": "Content",
    "schema": "On-site",
    "technical": "On-site",
    "eeat": "On-site",
    "entity": "Off-page",
    "ai_visibility": "Off-page",
}

# CTA style per pillar: on-page fixes are actionable ("draft"), off-page ones are
# review/monitor ("open"). Cosmetic — the button drives a task status transition.
PILLAR_KIND = {
    "content": "draft",
    "schema": "draft",
    "technical": "draft",
    "eeat": "draft",
    "entity": "open",
    "ai_visibility": "open",
}


# Statuses that mean "this task is still on the user's plate". An open task for a
# finding must never be duplicated by a later run; a finished one may legitimately
# come back, because the finding reappearing is a regression worth re-raising.
_OPEN_STATUSES = (UserAction.ActionStatus.PENDING, UserAction.ActionStatus.IN_PROGRESS)


def _identity(finding_code: str, title: str) -> str:
    """Stable identity for a task across runs.

    Deliberately ``(finding_code, title)`` and not the code alone. Several
    generators legitimately emit many distinct tasks under one code - GEO signals
    produce a "Win the AI query: <query>" task per losing prompt and a "Get
    mentioned on <domain>" task per citation gap. Keying on the code alone would
    collapse three real tasks into one and silently lose work.

    The title carries the distinguishing detail in those cases and is identical
    for a genuinely recurring finding, which is exactly the duplicate we want to
    suppress.
    """
    return f"{(finding_code or '').strip()}|{(title or '').strip()}"


def _finding_identity(rec) -> str:
    return _identity(getattr(rec, "finding_code", "") or "", getattr(rec, "title", "") or "")


def _open_identities_for_org(run) -> set[str]:
    """Finding identities the org already has an open task for.

    Every analysis creates a fresh set of Recommendation rows, and the Actions
    list is org-scoped across all runs, so materialising per-run with no
    cross-run check showed the same task once per analysis. Three runs of the
    same site meant three "Add Publish Date" tasks.
    """
    org_id = getattr(run, "organization_id", None)
    if not org_id:
        return set()

    rows = (
        UserAction.objects.filter(
            analysis_run__organization_id=org_id,
            status__in=_OPEN_STATUSES,
        )
        .exclude(analysis_run_id=run.id)
        .values_list("recommendation__finding_code", "title")
    )
    return {_identity(code or "", title or "") for code, title in rows}


def materialize_run_actions(run, owner_email: str) -> tuple[int, int]:
    """Create UserAction tasks for ``run``'s recommendations not yet materialized.

    Deduplicates on two axes:
      * within the run, by recommendation id (a re-sync is idempotent);
      * across the org's other runs, by finding identity, so re-analysing a site
        does not re-raise a task the user already has open.

    Returns ``(created, total)`` where ``total`` is the run's task count after sync.
    """
    existing_rec_ids = set(
        UserAction.objects.filter(analysis_run=run)
        .exclude(recommendation__isnull=True)
        .values_list("recommendation_id", flat=True)
    )
    open_elsewhere = _open_identities_for_org(run)

    to_create = []
    skipped_as_duplicate = 0
    seen_identities: set[str] = set()
    for rec in run.recommendations.all():
        if rec.id in existing_rec_ids:
            continue
        identity = _finding_identity(rec)
        # A rec with neither code nor title has no identity to compare on; let it
        # through rather than collapsing unrelated rows onto an empty key.
        has_identity = identity != _identity("", "")
        # Guard against the same finding arriving twice inside one run too.
        if has_identity and (identity in open_elsewhere or identity in seen_identities):
            skipped_as_duplicate += 1
            continue
        seen_identities.add(identity)
        to_create.append(
            UserAction(
                user_email=owner_email,
                analysis_run=run,
                recommendation=rec,
                action_type=PILLAR_ACTION_TYPE.get(rec.pillar, UserAction.ActionType.ADD_STRUCTURE),
                title=rec.title[:255],
                description=rec.description,
                points_value=rec.xp_reward or 10,
                status=UserAction.ActionStatus.PENDING,
            )
        )

    if to_create:
        UserAction.objects.bulk_create(to_create)
    if skipped_as_duplicate:
        logger.info(
            "Run %s: skipped %d task(s) already open elsewhere in the org",
            run.id,
            skipped_as_duplicate,
        )
    total = UserAction.objects.filter(analysis_run=run).count()
    return len(to_create), total
