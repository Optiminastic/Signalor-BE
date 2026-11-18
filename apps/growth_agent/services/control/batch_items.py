"""
Putting drafts into the brand's review batch for the day (see ``batches``).
Kept apart from ``batches`` so drafting and editing can call it without
importing the decision service.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from ...models import ActionVersion, ApprovalBatch, ApprovalBatchItem, GrowthAction


def _batch_for_today(action: GrowthAction) -> ApprovalBatch:
    day = timezone.localdate()
    found = ApprovalBatch.objects.filter(organization_id=action.organization_id, day=day).first()
    if found is not None:
        return found
    try:
        with transaction.atomic():
            return ApprovalBatch.objects.create(organization_id=action.organization_id, day=day)
    except IntegrityError:  # created by a concurrent draft a moment ago
        return ApprovalBatch.objects.get(organization_id=action.organization_id, day=day)


def add(
    action: GrowthAction, version: ActionVersion, reason: str = ApprovalBatchItem.Reason.PREPARED
) -> None:
    """Put this exact version in the brand's batch for today (an edit adds a new item)."""
    batch = _batch_for_today(action)
    ApprovalBatchItem.objects.get_or_create(
        organization_id=action.organization_id,
        batch=batch,
        action=action,
        version=version,
        defaults={"added_reason": reason},
    )
