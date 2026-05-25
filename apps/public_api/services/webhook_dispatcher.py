"""
Outbound webhook dispatcher.

Signs the payload with HMAC-SHA256 using the webhook's secret and POSTs to
the subscriber's URL. Each (webhook, event, resource) gets at most one
WebhookDelivery row — the unique_together constraint guarantees the signal
can fire freely without producing duplicates.

Retries: simple in-thread backoff (1s, 4s, 16s). Good enough for v1; if
delivery volume grows we'd move this to Celery or RQ.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from typing import Any

import requests
from django import db
from django.utils import timezone

from ..models import Webhook, WebhookDelivery

logger = logging.getLogger("apps")

# Header names mirror what we already publish on inbound webhooks
# (see apps.integrations Shopify HMAC) so partners only learn one scheme.
HEADER_SIGNATURE = "X-Signalor-Signature"
HEADER_EVENT = "X-Signalor-Event"
HEADER_DELIVERY = "X-Signalor-Delivery"
HEADER_TIMESTAMP = "X-Signalor-Timestamp"

RETRY_DELAYS_SECONDS = (1, 4, 16)
DELIVERY_TIMEOUT_SECONDS = 10


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    # Sign timestamp + "." + body. Same shape as Stripe/Shopify so partner
    # SDKs can be borrowed with minimal changes.
    payload = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _attempt_post(webhook: Webhook, event: str, body: bytes, delivery_id: int) -> tuple[int | None, str, str]:
    timestamp = str(int(time.time()))
    signature = _sign(webhook.get_secret(), timestamp, body)
    headers = {
        "Content-Type": "application/json",
        HEADER_EVENT: event,
        HEADER_DELIVERY: str(delivery_id),
        HEADER_TIMESTAMP: timestamp,
        HEADER_SIGNATURE: f"sha256={signature}",
    }
    try:
        resp = requests.post(
            webhook.url,
            data=body,
            headers=headers,
            timeout=DELIVERY_TIMEOUT_SECONDS,
        )
        return resp.status_code, (resp.text or "")[:500], ""
    except requests.RequestException as exc:
        return None, "", str(exc)[:500]


def _deliver(delivery_id: int) -> None:
    try:
        delivery = WebhookDelivery.objects.select_related("webhook").get(pk=delivery_id)
    except WebhookDelivery.DoesNotExist:
        return

    webhook = delivery.webhook
    body = _build_body(delivery)

    last_status: int | None = None
    last_body: str = ""
    last_error: str = ""

    for attempt_idx, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
        if attempt_idx > 1:
            time.sleep(delay)
        status_code, body_preview, error = _attempt_post(webhook, delivery.event, body, delivery.pk)
        delivery.attempts = attempt_idx
        last_status, last_body, last_error = status_code, body_preview, error

        if status_code is not None and 200 <= status_code < 300:
            delivery.status = WebhookDelivery.Status.SUCCESS
            delivery.response_status = status_code
            delivery.response_body_preview = body_preview
            delivery.error_message = ""
            delivery.delivered_at = timezone.now()
            delivery.save(
                update_fields=[
                    "status",
                    "attempts",
                    "response_status",
                    "response_body_preview",
                    "error_message",
                    "delivered_at",
                ]
            )
            Webhook.objects.filter(pk=webhook.pk).update(last_delivered_at=timezone.now())
            return

    delivery.status = WebhookDelivery.Status.FAILED
    delivery.response_status = last_status
    delivery.response_body_preview = last_body
    delivery.error_message = last_error
    delivery.delivered_at = timezone.now()
    delivery.save(
        update_fields=[
            "status",
            "attempts",
            "response_status",
            "response_body_preview",
            "error_message",
            "delivered_at",
        ]
    )


def _build_body(delivery: WebhookDelivery) -> bytes:
    """Re-fetch fresh resource state at delivery time so retries reflect the
    latest data (not a snapshot from when the signal fired)."""
    from apps.analyzer.models import AnalysisRun

    payload: dict[str, Any] = {
        "event": delivery.event,
        "delivery_id": delivery.pk,
        "created_at": delivery.created_at.isoformat(),
    }
    if delivery.event == Webhook.Event.ANALYSIS_COMPLETED:
        try:
            run = AnalysisRun.objects.get(slug=delivery.resource_id)
            payload["data"] = {
                "slug": run.slug,
                "url": run.url,
                "status": run.status,
                "score": run.composite_score,
                "completed_at": run.updated_at.isoformat(),
            }
        except AnalysisRun.DoesNotExist:
            payload["data"] = {"slug": delivery.resource_id, "status": "missing"}
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _deliver_threadsafe(delivery_id: int) -> None:
    try:
        _deliver(delivery_id)
    finally:
        # Long-running background threads must close stale DB connections —
        # see ranking-be/CLAUDE.md pitfalls.
        db.close_old_connections()


def dispatch_event(event: str, organization_id: int, resource_id: str) -> None:
    """Enqueue delivery to every active webhook in the org subscribed to ``event``.

    Idempotent: WebhookDelivery has a unique constraint on
    (webhook, event, resource_id), so multiple calls produce a single delivery.
    """
    webhooks = list(
        Webhook.objects.filter(
            organization_id=organization_id,
            is_active=True,
        )
    )
    for webhook in webhooks:
        if not webhook.subscribes_to(event):
            continue
        delivery, created = WebhookDelivery.objects.get_or_create(
            webhook=webhook,
            event=event,
            resource_id=resource_id,
        )
        if not created:
            continue
        thread = threading.Thread(
            target=_deliver_threadsafe,
            args=(delivery.pk,),
            daemon=True,
        )
        thread.start()
