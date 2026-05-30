"""Core drip-queue logic, callable from both the management command and the
APScheduler background job. Keep this dependency-light so it stays cheap to
invoke many times per hour."""
import logging
from dataclasses import dataclass

from django.utils import timezone

from .email_sender import send_drip_email
from .models import PricingDripState
from .scheduling import (
    MAX_SEND_FAILURES,
    MIN_INTER_EMAIL_GAP,
    STEP_OFFSETS,
    TOTAL_STEPS,
)

logger = logging.getLogger("apps")


@dataclass
class DripRunResult:
    sent: int
    skipped: int
    failed: int

    def as_dict(self) -> dict:
        return {"sent": self.sent, "skipped": self.skipped, "failed": self.failed}


def process_drip_queue(*, dry_run: bool = False, email_filter: str = "") -> DripRunResult:
    """Send the next-due drip email to each eligible non-suppressed user.

    Returns counters for observability. Idempotent and overlap-safe — each
    row's `current_step` advance is gated by `current_step < N` so two
    concurrent invocations cannot double-send the same step.
    """
    now = timezone.now()
    qs = PricingDripState.objects.filter(suppressed=False, current_step__lt=TOTAL_STEPS)
    if email_filter:
        qs = qs.filter(email=email_filter.lower().strip())

    sent = skipped = failed = 0
    for state in qs.iterator():
        next_step = state.current_step + 1
        due_at = state.entered_at + STEP_OFFSETS[next_step]
        if now < due_at:
            skipped += 1
            continue
        # Anti-burst guard: if the previous send happened recently, defer this
        # step. Without this, a scheduler that was offline for >24h would
        # blast Emails 1-4 at 5-min intervals as soon as it resumed.
        if state.last_sent_at and (now - state.last_sent_at) < MIN_INTER_EMAIL_GAP:
            skipped += 1
            continue
        if dry_run:
            logger.info(
                "[dry-run] step=%s -> %s (entered_at=%s, due_at=%s)",
                next_step, state.email, state.entered_at.isoformat(), due_at.isoformat(),
            )
            sent += 1
            continue
        ok = send_drip_email(state, next_step)
        if ok:
            sent += 1
            continue
        failed += 1
        # Track consecutive failures so a stuck row (bad recipient, ISP block)
        # gets auto-suppressed instead of retrying every tick forever.
        state.failure_count += 1
        if state.failure_count >= MAX_SEND_FAILURES:
            state.suppressed = True
            state.suppressed_reason = "send_failed"
            state.suppressed_at = timezone.now()
            state.save(update_fields=[
                "failure_count", "suppressed", "suppressed_reason",
                "suppressed_at", "updated_at",
            ])
            logger.warning(
                "Drip auto-suppressed for %s after %s consecutive send failures",
                state.email, state.failure_count,
            )
        else:
            state.save(update_fields=["failure_count", "updated_at"])

    return DripRunResult(sent=sent, skipped=skipped, failed=failed)
