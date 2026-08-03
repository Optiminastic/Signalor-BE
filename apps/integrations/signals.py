"""Integration-side reactions to analyzer events.

Registered from IntegrationsConfig.ready(). Kept here rather than in
apps/public_api/signals.py so the analyzer and the public API stay unaware of
which notification channels exist - adding Teams or Discord is a new branch in
this file and nothing else.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.analyzer.models import AnalysisRun

logger = logging.getLogger("apps")


@receiver(post_save, sender=AnalysisRun, dispatch_uid="integrations.slack.analysis_complete")
def notify_channels_on_complete(sender, instance: AnalysisRun, created: bool, **kwargs) -> None:
    """Post the report to any connected channel when a run completes.

    ``dispatch_uid`` guards against double-registration under autoreload. The
    notifier itself swallows failures, so a Slack outage cannot affect the save.
    """
    if instance.status != AnalysisRun.Status.COMPLETE or not instance.organization_id:
        return
    from .services.slack.notify import notify_analysis_complete

    notify_analysis_complete(instance)
