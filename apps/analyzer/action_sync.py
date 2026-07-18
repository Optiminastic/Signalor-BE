"""Materialize a run's Recommendations into UserAction tasks (idempotent).

One authoritative implementation shared by:
- ``SyncActionsView`` (dashboard-driven, on demand), and
- ``run_scheduled_analyses`` (weekly cron),
so a scheduled run updates the Tasks queue without waiting for the dashboard to sync.

Idempotent: a Recommendation already materialized into a UserAction for the run is
skipped, so it is safe to call repeatedly.
"""

from __future__ import annotations

from .models import UserAction

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


def materialize_run_actions(run, owner_email: str) -> tuple[int, int]:
    """Create UserAction tasks for ``run``'s recommendations not yet materialized.

    Returns ``(created, total)`` where ``total`` is the run's task count after sync.
    """
    existing_rec_ids = set(
        UserAction.objects.filter(analysis_run=run)
        .exclude(recommendation__isnull=True)
        .values_list("recommendation_id", flat=True)
    )
    to_create = [
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
        for rec in run.recommendations.all()
        if rec.id not in existing_rec_ids
    ]
    if to_create:
        UserAction.objects.bulk_create(to_create)
    total = UserAction.objects.filter(analysis_run=run).count()
    return len(to_create), total
