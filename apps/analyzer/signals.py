"""Analyzer receivers on other apps' models.

Lives in ``analyzer`` (not ``organizations``) because ``ScheduledAnalysis`` is an
analyzer model: analyzer already depends on organizations, and registering here
keeps that dependency pointing one way.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.organizations.models import Organization

from .enrollment import enroll_organization

logger = logging.getLogger("apps")


@receiver(post_save, sender=Organization, dispatch_uid="analyzer_enroll_weekly_analysis")
def enroll_weekly_analysis(sender, instance: Organization, created: bool, **kwargs) -> None:
    """Give every new brand a weekly analysis schedule.

    A signal rather than a call in each creation path: brands are created in more
    than one place (the organizations serializer and the integrations connect
    flow), and this is the one choke point that a future third path can't miss.

    Never raises. The receiver runs inside the org-creation transaction, so
    letting an exception escape would fail the user's brand creation over a
    scheduling side-effect — enrollment is recoverable (the backfill command
    re-runs it), brand creation is not.
    """
    if not created:
        return
    try:
        enroll_organization(instance)
    except Exception:
        logger.exception("weekly analysis enrollment failed for org %s", instance.pk)
