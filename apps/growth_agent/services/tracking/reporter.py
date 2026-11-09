"""
Weekly progress email for a completed scheduled snapshot.

Plain text through Django's configured mail backend (provider-agnostic).
Failures are logged and never fail the snapshot itself.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail

from ...models import GrowthTask, WeeklySnapshot

logger = logging.getLogger("apps")
MAX_LISTED = 5


def _signed(value: float) -> str:
    return f"+{value}" if value > 0 else f"{value}"


def build_body(snapshot: WeeklySnapshot, brand: str, done_count: int, next_titles: list[str]) -> str:
    cycle = snapshot.cycle
    lines = [
        f"Week {snapshot.week} progress for {brand}",
        "",
        f"AI visibility: {snapshot.share_of_voice}% ({_signed(snapshot.delta_vs_baseline)} pts vs "
        f"{cycle.baseline_share_of_voice}% baseline, goal {cycle.target_share_of_voice}%)",
        f"Answers checked: {snapshot.answers_checked}",
        f"Tasks completed so far: {done_count}",
    ]
    if snapshot.closed_gaps:
        lines += ["", "Sites that now cite you:", *(f"- {d}" for d in snapshot.closed_gaps[:MAX_LISTED])]
    if snapshot.wins:
        lines += [
            "",
            "Tasks that likely contributed:",
            *(f"- {w['title']}" for w in snapshot.wins[:MAX_LISTED]),
        ]
    if next_titles:
        lines += ["", "Next up:", *(f"- {t}" for t in next_titles[:MAX_LISTED])]
    return "\n".join(lines)


def send_weekly_email(snapshot: WeeklySnapshot) -> None:
    cycle = snapshot.cycle
    program = cycle.program
    brand = program.organization.name
    done = cycle.tasks.filter(status=GrowthTask.Status.DONE).count()
    upcoming = cycle.tasks.filter(status=GrowthTask.Status.PLANNED, week__gt=snapshot.week).values_list(
        "title", flat=True
    )
    try:
        send_mail(
            subject=f"{brand}: week {snapshot.week} AI visibility {snapshot.share_of_voice}%",
            message=build_body(snapshot, brand, done, list(upcoming[:MAX_LISTED])),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[program.created_by_email],
        )
    except Exception:
        logger.exception("growth weekly email failed for snapshot %s", snapshot.id)
