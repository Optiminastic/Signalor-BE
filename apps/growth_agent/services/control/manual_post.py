"""The owner posted an approved social post by hand.

Until Buffer is connected, approved social posts wait. The owner can copy one,
publish it on the platform themselves, and mark it posted here: the action is
closed as done so it is never scheduled again once Buffer is connected. The
owner's word is recorded with who and when; Signalor made no outside call.
"""

from __future__ import annotations

from django.db import transaction

from ...models import AgentActionLog, GrowthAction, GrowthProgram, GrowthTask
from ..execution import audit
from . import actions
from .approvals import ActionNotFound, DecisionRefused

POSTED_BY_OWNER = "posted_by_owner"
NOT_APPROVED = "Only an approved social post can be marked as posted."


def mark_posted(program: GrowthProgram, public_id: str, actor_email: str) -> GrowthAction:
    with transaction.atomic():
        action = (
            actions.lock_actions()
            .select_related("task")
            .filter(organization=program.organization, public_id=public_id)
            .first()
        )
        if action is None:
            raise ActionNotFound(public_id)
        if (
            action.kind != GrowthAction.Kind.PUBLISH_SOCIAL_POST
            or action.state != GrowthAction.State.APPROVED
        ):
            raise DecisionRefused("not_approved_social_post", NOT_APPROVED)
        action.state = GrowthAction.State.SUCCEEDED
        action.save(update_fields=["state", "updated_at"])
        task = action.task
        task.status = GrowthTask.Status.DONE
        task.save(update_fields=["status", "updated_at"])
        detail = {"action": action.public_id, "platform": action.current_version.payload.get("platform", "")}
        audit.record(
            program, task, POSTED_BY_OWNER, AgentActionLog.Outcome.SUCCEEDED, detail, actor_email=actor_email
        )
    return action
