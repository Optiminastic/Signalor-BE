"""Enroll organizations into their recurring analysis.

One authoritative implementation shared by:
- the ``Organization`` post_save receiver (``signals.py``), for new brands, and
- the ``enroll_scheduled_analyses`` command, for brands created before enrollment
  existed.

Without this, ``ScheduledAnalysis`` rows were only ever written by
``ScheduledAnalysisView``, which no client calls — so no brand had a schedule and
``run_scheduled_analyses`` never found work to do.

Idempotent: ``ScheduledAnalysis`` is unique on ``(organization, email)``, so
re-enrolling an already-scheduled brand is a no-op that leaves its existing
cadence (and any user-chosen frequency) untouched.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from .models import ScheduledAnalysis

WEEKLY_INTERVAL = timedelta(days=7)


def initial_next_run_at(org_id: int, *, now: datetime | None = None, spread_days: int = 0) -> datetime:
    """First run roughly a week out, jittered deterministically by ``org_id``.

    The jitter is what keeps a bulk enrollment from stampeding: without it every
    backfilled brand lands in the same 30-minute cron tick and fires a full
    (LLM-backed) analysis at once. Hours-only by default — enough to spread an
    organic signup burst — with ``spread_days`` widening it across the week for
    the backfill, where no brand is expecting a particular first-run time.
    """
    base = (now or timezone.now()) + WEEKLY_INTERVAL
    jitter = timedelta(hours=org_id % 24)
    if spread_days > 0:
        jitter += timedelta(days=org_id % spread_days)
    return base + jitter


def enroll_organization(org, *, spread_days: int = 0) -> ScheduledAnalysis | None:
    """Ensure ``org`` has a weekly schedule. Returns it, or None if not enrollable.

    A brand with no URL has nothing to analyze — onboarding creates those, so
    skipping is normal rather than an error. It gets enrolled by the backfill
    once a URL is set.
    """
    if not (org.url or "").strip():
        return None

    schedule, _created = ScheduledAnalysis.objects.get_or_create(
        organization=org,
        email=org.owner_email,
        defaults={
            "url": org.url,
            "brand_name": org.name or "",
            "frequency": ScheduledAnalysis.Frequency.WEEKLY,
            "next_run_at": initial_next_run_at(org.id, spread_days=spread_days),
            "is_active": True,
        },
    )
    return schedule
