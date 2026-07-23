"""Task (UserAction) verification — turn "done" into a live-site check.

A task is only trustworthy as *done* once its finding is confirmed gone from the
live page. This module centralises that: it re-crawls (or accepts a re-crawl
result), flips the task to VERIFIED, records why, and awards its points **only on
verification** (so an unconfirmed "Mark complete" can't inflate the score).

Used by:
  - the on-demand "Verify" button (VerifyActionView),
  - the PR-merge webhook (best-effort fast path — a merge may not be deployed yet),
  - the daily recheck_recommendations job (the reliable safety net).
"""

from __future__ import annotations

import logging

from django.utils import timezone

from .models import UserAction

logger = logging.getLogger("apps")

_SAVE_FIELDS = ["status", "verified_at", "verification_message", "score_after", "score_improvement", "updated_at"]


def _award_points(action: UserAction) -> None:
    """Award the task's points to the owner's gamification profile (idempotent per verify)."""
    from .models import UserGamification

    try:
        gamification, _ = UserGamification.objects.get_or_create(
            user_email=action.user_email, defaults={"user_email": action.user_email}
        )
        gamification.add_points(action.points_value)
    except Exception:  # noqa: BLE001 — gamification is best-effort, never block a verify
        logger.exception("Failed to award points for action %s", action.id)


def mark_action_verified(action: UserAction, message: str = "", score_after: float | None = None) -> None:
    """Flip a task to VERIFIED, stamp the result, and award its points once."""
    already_verified = action.status == UserAction.ActionStatus.VERIFIED
    action.status = UserAction.ActionStatus.VERIFIED
    if not action.verified_at:
        action.verified_at = timezone.now()
    if message:
        action.verification_message = message
    if score_after is not None:
        action.score_after = score_after
        if action.score_before is not None:
            action.score_improvement = score_after - action.score_before
    action.save(update_fields=_SAVE_FIELDS)
    if not already_verified:
        _award_points(action)


def verify_and_mark(action: UserAction, url: str, finding_code: str, pillar: str = "") -> dict:
    """Re-crawl `url` for `finding_code` and update the task from the result."""
    from .pipeline.verify import verify_finding

    result = verify_finding(url, finding_code, pillar)
    if result.get("verified"):
        mark_action_verified(action, result.get("message", ""))
    else:
        action.verification_message = result.get("message", "")
        action.save(update_fields=["verification_message", "updated_at"])
    return result


def verify_action(action: UserAction) -> dict:
    """Verify one task against the live site, resolving its finding from the task."""
    rec = action.recommendation
    run = action.analysis_run or (rec.analysis_run if rec else None)
    url = getattr(run, "url", "") or ""
    finding_code = (rec.finding_code if rec else "") or ""
    pillar = (getattr(rec, "pillar", "") if rec else "") or ""
    if not url or not finding_code:
        return {
            "verified": False,
            "message": "This task has no finding to verify against — mark it complete manually.",
            "skipped": True,
        }
    return verify_and_mark(action, url, finding_code, pillar)


def action_targets_for_findings(run_id: int, finding_codes: list[str]) -> list[tuple[int, str, str]]:
    """(action_id, finding_code, pillar) for a run's unverified tasks matching these findings.

    Capture this BEFORE any re-analysis: re-analysis bulk-recreates recommendations
    and can null the task→recommendation link, so we snapshot the finding here.
    """
    if not finding_codes:
        return []
    qs = (
        UserAction.objects.filter(
            analysis_run_id=run_id, recommendation__finding_code__in=list(finding_codes)
        )
        .exclude(status=UserAction.ActionStatus.VERIFIED)
        .select_related("recommendation")
    )
    return [
        (a.id, a.recommendation.finding_code, getattr(a.recommendation, "pillar", "") or "")
        for a in qs
        if a.recommendation
    ]


def verify_captured_targets(url: str, targets: list[tuple[int, str, str]]) -> int:
    """Verify pre-captured (action_id, finding_code, pillar) targets. Returns count verified."""
    if not url or not targets:
        return 0
    verified = 0
    for action_id, finding_code, pillar in targets:
        action = UserAction.objects.filter(pk=action_id).first()
        if not action:
            continue
        try:
            if verify_and_mark(action, url, finding_code, pillar).get("verified"):
                verified += 1
        except Exception:  # noqa: BLE001
            logger.exception("Task verify failed for action %s", action_id)
    return verified
