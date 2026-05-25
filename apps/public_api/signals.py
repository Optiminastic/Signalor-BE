"""
Signal handlers that turn internal state changes into outbound webhook events.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.analyzer.models import AnalysisRun

from .models import Webhook
from .services.webhook_dispatcher import dispatch_event

logger = logging.getLogger("apps")


@receiver(post_save, sender=AnalysisRun)
def fire_analysis_completed(sender, instance: AnalysisRun, created: bool, **kwargs):
    # Dedupe is handled by WebhookDelivery's unique_together — we can fire
    # on every save without worrying about double-delivery.
    if instance.status != AnalysisRun.Status.COMPLETE:
        return
    if not instance.organization_id:
        # Anonymous free-tool runs have no org and can't subscribe webhooks.
        return
    try:
        dispatch_event(
            event=Webhook.Event.ANALYSIS_COMPLETED,
            organization_id=instance.organization_id,
            resource_id=instance.slug,
        )
    except Exception:
        logger.exception("webhook dispatch failed for run=%s", instance.pk)
