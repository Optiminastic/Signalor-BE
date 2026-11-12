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
AUDIT_DRAFTED_WITH_CONCERNS = "drafted_with_concerns"


# Separates an outreach email's message from its footer (sender line and opt-out).
FOOTER_SEPARATOR = "\n\n--\n"


def split_footer(body: str) -> tuple[str, str]:
    """An email body as (message, footer). The footer is everything after the
    last separator; a body without one has no footer."""
    index = body.rfind(FOOTER_SEPARATOR)
    if index < 0:
        return body, ""
    return body[:index], body[index + len(FOOTER_SEPARATOR) :]


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


EMAIL_KINDS = (GrowthAction.Kind.SEND_OUTREACH_EMAIL, GrowthAction.Kind.SEND_FOLLOW_UP)


def updates_task(action: GrowthAction) -> bool:
    """Whether this action's progress is reflected on its task.

    A follow-up belongs to an outreach task whose first email already went
    out; the task keeps that status, whatever happens to the follow-up.
    """
    return action.kind != GrowthAction.Kind.SEND_FOLLOW_UP


def create_action(task: GrowthTask, prepared: Prepared, *, thread=None, sequence_no=None) -> GrowthAction:
    """Persist a draft for ``task`` and leave it waiting for the owner's decision.

    A follow-up also names its outreach thread and step.
    """
    program = task.cycle.program
    with transaction.atomic():
        action = GrowthAction.objects.create(
            organization=program.organization,
            task=task,
            kind=prepared.kind,
            connector=prepared.connector,
            state=GrowthAction.State.AWAITING_APPROVAL,
            thread=thread,
            sequence_no=sequence_no,
        )
        version = ActionVersion.objects.create(
            action=action,
            number=FIRST_VERSION,
            payload=prepared.payload,
            destination=prepared.destination,
            payload_hash=content_hash(
                prepared.kind, prepared.connector, prepared.payload, prepared.destination
            ),
            review_notes=list(prepared.review_notes),
        )
        action.current_version = version
        action.save(update_fields=["current_version", "updated_at"])
        detail = {"action": action.public_id, "version": version.number, "kind": action.kind}
        if prepared.review_notes:
            detail["concerns"] = list(prepared.review_notes)
        audit_action = AUDIT_DRAFTED_WITH_CONCERNS if prepared.review_notes else AUDIT_PREPARED
        audit.record(program, task, audit_action, AgentActionLog.Outcome.SUCCEEDED, detail)
    return action


def latest_action(task: GrowthTask) -> GrowthAction | None:
    """The task's newest action, not counting follow-ups. Uses a prefetch when one was made."""
    own = [a for a in task.actions.all() if updates_task(a)]
    own.sort(key=lambda a: (a.created_at, a.id), reverse=True)
    return own[0] if own else None


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
            "answer": article.answer,
            "review_notes": version.review_notes,
        }
    message, footer = split_footer(version.payload["body"])
    return {
        "subject": version.payload["subject"],
        "body": version.payload["body"],
        # The editable part and the fixed footer (sender line and opt-out).
        "message": message,
        "footer": footer,
        "review_notes": version.review_notes,
        "recipient_domain": version.destination.get("domain", ""),
        "sender": version.destination.get("sender", ""),
    }
