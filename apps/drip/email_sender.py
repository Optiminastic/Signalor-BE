"""Render + dispatch one drip email for a given PricingDripState row."""
import logging
import random

from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils import timezone

from apps.accounts.amplitude_client import track_email_sent

from .models import DripSendLog, PricingDripState
from .scheduling import PLAIN_TEXT_STEPS, STEP_TEMPLATE_NAMES
from .subjects import SUBJECT_VARIANTS
from .unsubscribe import make_unsubscribe_url

logger = logging.getLogger("apps")

FRONTEND_URL = getattr(settings, "FRONTEND_URL", "") or "https://signalor.ai"


def _render_context(state: PricingDripState) -> dict:
    return {
        "first_name": state.first_name,
        "domain": state.domain,
        "geo_score": state.geo_score,
        "fix_count": state.fix_count,
        "top_competitor": state.top_competitor,
        "competitor_list": state.competitor_list,
        "cms_platform": state.cms_platform,
        "top_recommendation_title": state.top_recommendation_title,
        "issue_count": state.issue_count,
        "competitor_count": state.competitor_count,
        "pricing_url": f"{FRONTEND_URL.rstrip('/')}/pricing",
        "frontend_url": FRONTEND_URL.rstrip("/"),
        "logo_url": settings.SIGNALOR_LOGO_URL,
        "unsubscribe_url": make_unsubscribe_url(state.email),
    }


def _pick_subject(step: int, ctx: dict) -> tuple[str, str]:
    """Return (variant_letter, rendered_subject)."""
    variants = SUBJECT_VARIANTS[step]
    variant = random.choice(list(variants.keys()))
    template_str = variants[variant]
    # Subjects are short — render as inline Django Template.
    subject = Template(template_str).render(Context(ctx))
    return variant, subject


def send_drip_email(state: PricingDripState, step: int) -> bool:
    """Render and send the email for `step`, log the send, advance state.

    Returns True on send success, False otherwise. Caller is responsible for
    not advancing `current_step` on failure (so the next cron tick retries).
    """
    # Idempotency guard: if a successful send for this (state, step) is
    # already on file (e.g., a prior tick crashed *after* SendGrid accepted
    # the email but *before* current_step advanced), don't re-send. Advance
    # current_step in the caller path by returning True.
    if DripSendLog.objects.filter(state=state, step=step, success=True).exists():
        logger.warning(
            "Drip step %s already sent to %s — skipping duplicate send, advancing state",
            step, state.email,
        )
        state.current_step = step
        state.last_sent_at = timezone.now()
        state.failure_count = 0
        state.save(update_fields=["current_step", "last_sent_at", "failure_count", "updated_at"])
        return True

    ctx = _render_context(state)
    template_base = STEP_TEMPLATE_NAMES[step]
    variant, subject = _pick_subject(step, ctx)

    is_plain_only = step in PLAIN_TEXT_STEPS

    try:
        text_body = render_to_string(f"{template_base}.txt", ctx)
    except Exception as e:
        logger.exception("Drip step %s text template render failed for %s", step, state.email)
        DripSendLog.objects.create(
            state=state, step=step, subject_variant=variant, subject=subject,
            success=False, error=f"text template render: {e!r}",
        )
        return False

    unsubscribe_url = ctx["unsubscribe_url"]
    # RFC 8058 List-Unsubscribe-Post requires both header lines; Gmail and
    # Yahoo's 2024 bulk-sender requirements treat their absence as a spam
    # signal. We attach them to every drip — plain text (Email 4) AND HTML.
    list_unsubscribe_headers = {
        "List-Unsubscribe": (
            f"<{unsubscribe_url}>, "
            f"<mailto:{settings.FOUNDER_FROM_EMAIL}?subject=unsubscribe>"
        ),
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }

    if is_plain_only:
        from_email = f"{settings.FOUNDER_FROM_NAME} <{settings.FOUNDER_FROM_EMAIL}>"
        msg = EmailMessage(
            subject=subject,
            body=text_body,
            from_email=from_email,
            to=[state.email],
            headers={
                **list_unsubscribe_headers,
                # Keep open/click tracking off for the founder note so it
                # reads as a personal email, not a marketing blast.
                "X-SMTPAPI": '{"filters":{"clicktrack":{"settings":{"enable":0}},"opentrack":{"settings":{"enable":0}}}}',
            },
        )
        msg.content_subtype = "plain"
    else:
        try:
            html_body = render_to_string(f"{template_base}.html", ctx)
        except Exception as e:
            logger.exception("Drip step %s html template render failed for %s", step, state.email)
            DripSendLog.objects.create(
                state=state, step=step, subject_variant=variant, subject=subject,
                success=False, error=f"html template render: {e!r}",
            )
            return False

        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[state.email],
            headers=list_unsubscribe_headers,
        )
        msg.attach_alternative(html_body, "text/html")

    try:
        msg.send(fail_silently=False)
    except Exception as e:
        logger.exception("Drip step %s send failed for %s", step, state.email)
        DripSendLog.objects.create(
            state=state, step=step, subject_variant=variant, subject=subject,
            success=False, error=f"smtp send: {e!r}",
        )
        return False

    DripSendLog.objects.create(
        state=state, step=step, subject_variant=variant, subject=subject, success=True,
    )

    # Advance state. Reset failure_count so an isolated past blip doesn't
    # contribute toward the auto-suppress threshold once the row recovers.
    state.current_step = step
    state.last_sent_at = timezone.now()
    state.failure_count = 0
    state.save(update_fields=["current_step", "last_sent_at", "failure_count", "updated_at"])

    # Fire Amplitude email_sent (best-effort — failure is swallowed inside).
    # Fall back to the user's email as the Amplitude id so users who entered
    # the drip without an authenticated session still attribute downstream
    # checkout_started to the correct send + subject variant.
    amp_id = state.amplitude_user_id or state.email
    track_email_sent(
        user_id=amp_id,
        step=step,
        subject_variant=variant,
        template=template_base,
        domain=state.domain,
    )

    logger.info(
        "Drip step %s sent to %s (variant=%s, subject=%r)",
        step, state.email, variant, subject,
    )
    return True
