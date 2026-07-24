"""Build the Growth Agent's daily plan: today's ranked slice of a run's tasks.

The Growth Agent is a read model over ``UserAction`` — the same rows the Tasks
page shows, projected down to "what to do today". Ordering comes from the daily
re-check job (``daily_priority_rank`` / ``is_top_fix``); when that hasn't run yet
the fields are 0/False, so we fall back to raw priority rather than returning an
empty plan.

No ORM models are returned from the view — everything here is plain dicts.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.core.cache import cache
from django.utils import timezone

from .action_sync import PILLAR_GROUP, PILLAR_KIND, materialize_run_actions
from .models import ScheduledAnalysis, UserAction

DAILY_PLAN_SIZE = 5

# Manual "Refresh plan" is allowed once per this window. The plan is a *daily*
# artifact, and refresh re-runs the (LLM-free but not free) reprioritize pass, so
# one manual refresh a day is plenty. The nightly cron re-ranks regardless.
REFRESH_INTERVAL = timedelta(hours=24)


def _refresh_key(run) -> str:
    return f"agent_refresh:{run.id}"


def refresh_available_at(run) -> datetime | None:
    """When the plan may next be manually refreshed, or None if it's allowed now.

    Backed by a self-expiring cache entry keyed per run — a soft limit (a cache
    flush resets it), which is the right strength for a convenience action.
    """
    last = cache.get(_refresh_key(run))
    if not last:
        return None
    nxt = last + REFRESH_INTERVAL
    return nxt if nxt > timezone.now() else None


def mark_refreshed(run) -> None:
    cache.set(_refresh_key(run), timezone.now(), int(REFRESH_INTERVAL.total_seconds()))

_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_OPEN_STATUSES = (UserAction.ActionStatus.PENDING, UserAction.ActionStatus.IN_PROGRESS)
_DONE_STATUSES = (UserAction.ActionStatus.COMPLETED, UserAction.ActionStatus.VERIFIED)


def _sort_key(action: UserAction):
    """Top fix first, then ranked (1,2,3…), then unranked by priority.

    Unranked rows (rank 0, before the nightly job runs) sort after ranked ones and
    are ordered by the recommendation's own priority so the plan is still sensible.
    """
    rec = action.recommendation
    rank = rec.daily_priority_rank if rec else 0
    is_top = rec.is_top_fix if rec else False
    priority = _PRIORITY_ORDER.get(rec.priority, 4) if rec else 4
    # rank 0 => push to the back (after all genuinely-ranked rows)
    rank_sort = rank if rank > 0 else 10_000
    return (0 if is_top else 1, rank_sort, priority)


def _action_dict(action: UserAction) -> dict:
    rec = action.recommendation
    pillar = rec.pillar if rec else ""
    return {
        "action_id": action.id,
        "recommendation_id": rec.id if rec else None,
        "title": action.title,
        "description": action.description,
        "pillar": pillar,
        "group": PILLAR_GROUP.get(pillar, "On-site"),
        "priority": rec.priority if rec else "medium",
        "rank": rec.daily_priority_rank if rec else 0,
        "is_top_fix": rec.is_top_fix if rec else False,
        "impact": action.points_value,
        "effort": {
            "difficulty": rec.difficulty if rec else "",
            "minutes": rec.estimated_minutes if rec else 0,
        },
        "status": action.status,
        "kind": PILLAR_KIND.get(pillar, "open"),
    }


def _projected_score(run, open_actions: list[UserAction]) -> float | None:
    """The composite GEO score the run would reach if every open task were done.

    Grounded in each recommendation's ``impact_points`` — the headroom-clamped
    composite-score gain a fix recovers (see pipeline/impact.py). Summed over the
    open tasks and capped at 100, so it's a real projection, not a guess.
    """
    if run.composite_score is None:
        return None
    gain = sum(
        (a.recommendation.impact_points or 0.0) for a in open_actions if a.recommendation
    )
    return round(min(100.0, run.composite_score + gain), 1)


def _brief(run, projected_score: float | None) -> dict:
    org = run.organization
    next_at = None
    if org:
        sched = (
            ScheduledAnalysis.objects.filter(organization=org, is_active=True).order_by("next_run_at").first()
        )
        next_at = sched.next_run_at if sched else None
    return {
        "website": (org.url if org else "") or run.url,
        "brand_name": run.brand_name or (org.name if org else ""),
        "score": round(run.composite_score, 1) if run.composite_score is not None else None,
        "projected_score": projected_score,
        "last_analyzed_at": run.created_at,
        "next_analysis_at": next_at,
    }


def build_agent_plan(run, owner_email: str, *, today: date) -> dict:
    """Assemble the full plan payload for ``run``.

    Materializes the run's recommendations into tasks on first view so the page is
    never empty when the run has recommendations (idempotent — a no-op afterwards).
    """
    if not UserAction.objects.filter(analysis_run=run).exists():
        materialize_run_actions(run, owner_email)

    open_actions = list(
        UserAction.objects.filter(analysis_run=run, status__in=_OPEN_STATUSES).select_related(
            "recommendation"
        )
    )
    open_actions.sort(key=_sort_key)
    done_count = UserAction.objects.filter(analysis_run=run, status__in=_DONE_STATUSES).count()

    # Show the full ranked backlog grouped by display bucket (not just the top N):
    # the Growth Agent is the brand's whole task list for the run, ranked, with the
    # top ``DAILY_PLAN_SIZE`` called out as today's focus.
    groups: dict[str, list] = {}
    for action in open_actions:
        d = _action_dict(action)
        groups.setdefault(d["group"], []).append(d)

    # Group display order: Content, On-site, Off-page.
    order = {"Content": 0, "On-site": 1, "Off-page": 2}
    ordered_groups = sorted(groups.items(), key=lambda kv: order.get(kv[0], 9))

    top_fix = next(
        (_action_dict(a) for a in open_actions if a.recommendation and a.recommendation.is_top_fix),
        None,
    )

    next_refresh = refresh_available_at(run)
    return {
        "generated_for": today.isoformat(),
        "run_slug": run.slug,
        "brief": _brief(run, _projected_score(run, open_actions)),
        "top_fix": top_fix,
        "groups": [{"pillar": name, "actions": items} for name, items in ordered_groups],
        "counts": {
            "today": min(DAILY_PLAN_SIZE, len(open_actions)),
            "backlog": len(open_actions),
            "done": done_count,
        },
        # Null ⇒ refresh allowed now; otherwise the ISO time it next becomes allowed.
        "refresh_available_at": next_refresh.isoformat() if next_refresh else None,
    }
