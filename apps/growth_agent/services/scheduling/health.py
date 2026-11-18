"""
Is the Growth Agent actually running? Catches "the scheduler stopped" and
"the workers stopped", which otherwise fail silently for every brand.

Problems:
- the scheduler's heartbeat is older than ``STALE_AFTER`` (no round ran);
- an active brand has had no finished tick for ``STALE_AFTER``;
- an approved, due action has waited longer than ``SEND_WAIT_LIMIT``.

``alert`` logs each problem at error level (Sentry) and emails
``GROWTH_OPS_ALERT_EMAILS``, at most once per problem per ``ALERT_EVERY``
(Redis), so a long outage doesn't flood the inbox.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db.models import Max, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ...models import ApprovalDecision
from ..control.dispatch import dispatchable
from .round import HEARTBEAT_KEY, active_programs

logger = logging.getLogger("apps")

STALE_AFTER = timedelta(minutes=45)
SEND_WAIT_LIMIT = timedelta(minutes=60)
ALERT_EVERY = timedelta(hours=1)


@dataclass(frozen=True)
class Problem:
    key: str  # stable, for rate-limiting the alert
    message: str


def _scheduler(now: datetime) -> list[Problem]:
    raw = cache.get(HEARTBEAT_KEY)
    last = parse_datetime(raw) if raw else None
    if last is None or now - last > STALE_AFTER:
        when = last.isoformat() if last else "never"
        return [Problem("scheduler", f"The Growth Agent scheduler hasn't run a round since {when}.")]
    return []


def _brands(now: datetime) -> list[Problem]:
    cutoff = now - STALE_AFTER
    stale = active_programs().filter(
        Q(last_tick_at__lt=cutoff) | Q(last_tick_at__isnull=True, updated_at__lt=cutoff)
    )
    return [
        Problem(
            f"brand:{p.id}",
            f"Brand program {p.id} has had no finished round since {p.last_tick_at or 'it started'}.",
        )
        for p in stale.only("id", "last_tick_at")
    ]


def _waiting_sends(now: datetime) -> list[Problem]:
    approved_at = (
        ApprovalDecision.objects.filter(decision=ApprovalDecision.Decision.APPROVED)
        .values("action_id")
        .annotate(at=Max("created_at"))
    )
    oldest = {row["action_id"]: row["at"] for row in approved_at.filter(created_at__lt=now - SEND_WAIT_LIMIT)}
    late = dispatchable(now.date()).filter(pk__in=list(oldest)).values_list("public_id", flat=True)
    late = list(late)
    if not late:
        return []
    return [
        Problem("sends", f"{len(late)} approved action(s) have waited over an hour to send, e.g. {late[0]}.")
    ]


def problems(now: datetime | None = None) -> list[Problem]:
    now = now or timezone.now()
    return _scheduler(now) + _brands(now) + _waiting_sends(now)


def alert(found: list[Problem]) -> int:
    """Raise each problem, rate-limited. Returns how many alerts went out."""
    sent = 0
    recipients = list(getattr(settings, "GROWTH_OPS_ALERT_EMAILS", []))
    for problem in found:
        if not cache.add(f"growth:alerted:{problem.key}", "1", int(ALERT_EVERY.total_seconds())):
            continue
        logger.error("growth health: %s", problem.message)
        sent += 1
        if not recipients:
            continue
        try:
            send_mail("Growth Agent needs attention", problem.message, None, recipients)
        except Exception:  # noqa: BLE001 - an alert email failing must never stop the scheduler
            logger.exception("growth health: alert email failed")
    return sent
