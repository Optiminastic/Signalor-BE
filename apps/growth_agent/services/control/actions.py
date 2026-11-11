"""
Draft actions: the exact thing a task wants to send, stored before anyone
decides on it.

A draft is an immutable version with a hash over its content and destination.
The hash is what an approval is bound to, and what is re-checked before
sending, so a draft cannot be altered between approval and execution without
being noticed.
"""

from __future__ import annotations

import hashlib
import json

from django.db import transaction

from ...models import ActionVersion, AgentActionLog, GrowthAction, GrowthTask
from ..execution import audit
from ..execution.article import ArticleDraft, render_markdown
from ..execution.channel_base import Prepared

FIRST_VERSION = 1
AUDIT_PREPARED = "prepared"


def lock_actions():
    """Actions, row-locked on the action row only.

    ``current_version`` is nullable, so ``select_related`` reaches it with an
    outer join, and Postgres refuses ``FOR UPDATE`` on the nullable side of an
    outer join. Locking only the action's own row avoids that, and the action
    row is the one every decision and send serializes on.
    """
    return GrowthAction.objects.select_for_update(of=("self",))


def content_hash(kind: str, connector: str, payload: dict, destination: dict) -> str:
    """SHA-256 over a canonical encoding, so equal content always hashes equally."""
    canonical = json.dumps(
        {"kind": kind, "connector": connector, "payload": payload, "destination": destination},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def version_is_intact(action: GrowthAction, version: ActionVersion) -> bool:
    """Whether the stored content still matches the hash it was approved under."""
    recomputed = content_hash(action.kind, action.connector, version.payload, version.destination)
    return recomputed == version.payload_hash


def create_action(task: GrowthTask, prepared: Prepared) -> GrowthAction:
    """Persist a draft for ``task`` and leave it waiting for the owner's decision."""
    program = task.cycle.program
    with transaction.atomic():
        action = GrowthAction.objects.create(
            organization=program.organization,
            task=task,
            kind=prepared.kind,
            connector=prepared.connector,
            state=GrowthAction.State.AWAITING_APPROVAL,
        )
        version = ActionVersion.objects.create(
            action=action,
            number=FIRST_VERSION,
            payload=prepared.payload,
            destination=prepared.destination,
            payload_hash=content_hash(
                prepared.kind, prepared.connector, prepared.payload, prepared.destination
            ),
        )
        action.current_version = version
        action.save(update_fields=["current_version", "updated_at"])
        audit.record(
            program,
            task,
            AUDIT_PREPARED,
            AgentActionLog.Outcome.SUCCEEDED,
            {"action": action.public_id, "version": version.number, "kind": action.kind},
        )
    return action


def latest_action(task: GrowthTask) -> GrowthAction | None:
    """The task's newest action. Uses a prefetch when one was made."""
    actions = sorted(task.actions.all(), key=lambda a: (a.created_at, a.id), reverse=True)
    return actions[0] if actions else None


def present(action: GrowthAction, version: ActionVersion) -> dict:
    """What the brand owner is shown when reviewing a draft.

    Built only from the stored version, so it is exactly what would be sent.
    A recipient is shown by site domain; the address itself is never returned.
    """
    if action.kind == GrowthAction.Kind.PUBLISH_ARTICLE:
        article = ArticleDraft.model_validate(version.payload["article"])
        return {
            "title": article.title,
            "meta_description": article.meta_description,
            "slug": version.payload["slug"],
            "markdown": render_markdown(article, {}),
            "platform": version.destination.get("platform", ""),
        }
    return {
        "subject": version.payload["subject"],
        "body": version.payload["body"],
        "recipient_domain": version.destination.get("domain", ""),
        "sender": version.destination.get("sender", ""),
    }
