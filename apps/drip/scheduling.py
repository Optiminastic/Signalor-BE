"""Drip scheduling constants — single source of truth for the wait intervals.

Override via env for testing (e.g., set DRIP_FAST_MODE=1 to compress hours to
minutes so a full sequence completes in ~3 minutes).
"""
import os
from datetime import timedelta

FAST_MODE = os.getenv("DRIP_FAST_MODE", "").lower() in ("1", "true", "yes")


def assert_fast_mode_safe() -> None:
    """Refuse to run if DRIP_FAST_MODE is on and we're not in DEBUG.

    FAST_MODE compresses 30min/24h/48h/72h waits to 60s/120s/180s/240s. If
    that flag accidentally lands in prod env, every drip user gets a 4-email
    blast within 4 minutes. Called from the scheduler/management-command
    entry points so the failure is loud at startup, not silent at the first
    user-visible bug.
    """
    if not FAST_MODE:
        return
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    if not settings.DEBUG:
        raise ImproperlyConfigured(
            "DRIP_FAST_MODE=1 detected with DEBUG=False. Fast mode compresses "
            "the 4-email drip to 4 minutes — refusing to start with this combo "
            "to prevent a bulk send. Unset DRIP_FAST_MODE in this environment."
        )

if FAST_MODE:
    STEP_OFFSETS = {
        1: timedelta(seconds=60),
        2: timedelta(seconds=120),
        3: timedelta(seconds=180),
        4: timedelta(seconds=240),
    }
    # In fast mode, keep just enough gap to let a tester observe each send
    # before the next fires. Production uses a 20h gap (see else branch).
    MIN_INTER_EMAIL_GAP = timedelta(seconds=45)
else:
    STEP_OFFSETS = {
        1: timedelta(minutes=30),
        2: timedelta(hours=24),
        3: timedelta(hours=48),
        4: timedelta(hours=72),
    }
    # Anti-burst: if the scheduler is offline for days and resumes, this gap
    # forces it to deliver at most one step per ~day even though every step's
    # `due_at` already passed. Picked slightly under 24h so a healthy
    # scheduler's E2→E3 and E3→E4 cadence isn't artificially throttled.
    MIN_INTER_EMAIL_GAP = timedelta(hours=20)

STEP_TEMPLATE_NAMES = {
    1: "drip/email_1",
    2: "drip/email_2",
    3: "drip/email_3",
    4: "drip/email_4",
}

# Email 4 is plain text only (founder outreach style) — see brief.
PLAIN_TEXT_STEPS = {4}

TOTAL_STEPS = 4

# A row with this many consecutive send failures (typically permanent — invalid
# recipient, ISP block, suspended domain) is auto-suppressed to stop spinning
# on every tick. Picked at 5 so a brief transient blip (SendGrid 429s,
# upstream flap) doesn't lose a real user.
MAX_SEND_FAILURES = 5
