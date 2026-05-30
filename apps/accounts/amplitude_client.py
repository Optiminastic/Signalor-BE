import logging

from django.conf import settings

logger = logging.getLogger(__name__)
_client = None


def _client_or_none():
    global _client
    if not settings.AMPLITUDE_API_KEY:
        return None
    if _client is None:
        # Imported lazily so a missing dep / unset key never crashes import-time.
        from amplitude import Amplitude

        _client = Amplitude(settings.AMPLITUDE_API_KEY)
    return _client


def track_purchase_completed(*, user_id: str, plan: str, domain: str, amount_usd: float) -> None:
    client = _client_or_none()
    if client is None:
        logger.warning("Amplitude purchase_completed skipped: AMPLITUDE_API_KEY unset")
        return
    try:
        from amplitude import BaseEvent

        client.track(
            BaseEvent(
                event_type="purchase_completed",
                user_id=user_id,
                event_properties={
                    "plan": plan,
                    "domain": domain,
                    "amount_usd": amount_usd,
                },
            )
        )
        # Purchase events are low-frequency and high-value — flush immediately
        # so they don't sit in the SDK's batch queue when a worker recycles.
        client.flush()
        logger.info(
            "Amplitude purchase_completed queued: user=%s plan=%s amount=%s",
            user_id, plan, amount_usd,
        )
    except Exception:
        logger.exception("Amplitude purchase_completed emit failed")


def track_email_sent(
    *, user_id: str, step: int, subject_variant: str, template: str, domain: str
) -> None:
    """Server-side Amplitude event fired when the drip cron dispatches an email.

    Lets the funnel chart / A/B analysis attribute downstream `checkout_started`
    events back to the specific subject variant that drove them.
    """
    client = _client_or_none()
    if client is None:
        return
    try:
        from amplitude import BaseEvent

        client.track(
            BaseEvent(
                event_type="email_sent",
                user_id=user_id,
                event_properties={
                    "template": template,
                    "step": step,
                    "subject_variant": subject_variant,
                    "domain": domain,
                },
            )
        )
        client.flush()
    except Exception:
        logger.exception("Amplitude email_sent emit failed")
