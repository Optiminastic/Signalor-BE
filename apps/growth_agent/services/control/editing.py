"""
Editing a draft before it is sent.

An edit never changes a version. It creates the next version with the same
destination and puts the action back in front of the owner, so any approval
of an earlier version stops counting (approvals are bound to a version and its
hash). Only content can change:

- an outreach email: its subject and message. The footer, which names the
  sender and carries the opt-out line, is kept exactly as prepared and is
  re-attached by the server, so an edit can never drop it.
- an article: its title, meta description, slug and opening answer. Sections
  and FAQ are kept as they are.

The checks here are deterministic rules on the owner's own wording; no model
is involved.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from ...models import ActionVersion, AgentActionLog, GrowthAction, GrowthProgram, GrowthTask
from ..execution import audit
from ..execution.article import SLUG_MAX_LENGTH, ArticleDraft, slugify
from . import actions
from .actions import FOOTER_SEPARATOR, split_footer
from .approvals import ActionNotFound, DecisionRefused, matches_current

SUBJECT_MAX = 200
MESSAGE_MAX = 5000
TITLE_MAX = 150
META_DESCRIPTION_MAX = 160
ANSWER_MAX = 1000
AUDIT_EDITED = "edited"
INVALID = "invalid_edit"

EDITABLE_STATES = (
    GrowthAction.State.AWAITING_APPROVAL,
    GrowthAction.State.APPROVED,
    GrowthAction.State.REJECTED,
)
EMAIL_FIELDS = frozenset({"subject", "message"})
ARTICLE_FIELDS = frozenset({"title", "meta_description", "slug", "answer"})


@dataclass(frozen=True)
class EditRequest:
    version_number: int
    payload_hash: str
    fields: dict
    actor_email: str


def _refuse(message: str) -> DecisionRefused:
    return DecisionRefused(INVALID, message)


def _require_fields(fields: dict, allowed: frozenset[str]) -> None:
    keys = set(fields)
    if keys - allowed:
        raise _refuse(f"These fields can't be edited: {', '.join(sorted(keys - allowed))}.")
    if allowed - keys:
        raise _refuse(f"Missing fields: {', '.join(sorted(allowed - keys))}.")


def _text(fields: dict, name: str, limit: int, *, single_line: bool = False) -> str:
    value = str(fields[name]).strip()
    if not value:
        raise _refuse(f"{name.replace('_', ' ').capitalize()} can't be empty.")
    if len(value) > limit:
        raise _refuse(f"{name.replace('_', ' ').capitalize()} is longer than {limit} characters.")
    if single_line and ("\n" in value or "\r" in value):
        raise _refuse(f"{name.replace('_', ' ').capitalize()} must be one line.")
    return value


def _email_payload(current: dict, fields: dict) -> dict:
    _require_fields(fields, EMAIL_FIELDS)
    subject = _text(fields, "subject", SUBJECT_MAX, single_line=True)
    message = _text(fields, "message", MESSAGE_MAX)
    _, footer = split_footer(current["body"])
    if not footer:
        # Never send an email without the sender line and opt-out.
        raise _refuse("This draft has no opt-out footer, so it can't be edited.")
    return {**current, "subject": subject, "body": f"{message}{FOOTER_SEPARATOR}{footer}"}


def _article_payload(current: dict, fields: dict) -> dict:
    _require_fields(fields, ARTICLE_FIELDS)
    slug = _text(fields, "slug", SLUG_MAX_LENGTH, single_line=True)
    if slugify(slug) != slug:
        raise _refuse("The slug may only use lowercase letters, digits and single hyphens.")
    article = ArticleDraft.model_validate(current["article"]).model_copy(
        update={
            "title": _text(fields, "title", TITLE_MAX, single_line=True),
            "meta_description": _text(fields, "meta_description", META_DESCRIPTION_MAX, single_line=True),
            "answer": _text(fields, "answer", ANSWER_MAX),
        }
    )
    return {**current, "article": article.model_dump(), "slug": slug}


def edited_payload(action: GrowthAction, fields: dict) -> dict:
    current = action.current_version.payload
    if action.kind in actions.EMAIL_KINDS:
        return _email_payload(current, fields)
    if action.kind == GrowthAction.Kind.PUBLISH_ARTICLE:
        return _article_payload(current, fields)
    raise _refuse("This kind of action can't be edited.")


def _locked_action(program: GrowthProgram, public_id: str) -> GrowthAction:
    action = (
        actions.lock_actions()
        .select_related("current_version", "task")
        .filter(organization=program.organization, public_id=public_id)
        .first()
    )
    if action is None:
        raise ActionNotFound(public_id)
    return action


def _back_to_review(action: GrowthAction, version: ActionVersion) -> None:
    action.current_version = version
    action.state = GrowthAction.State.AWAITING_APPROVAL
    action.save(update_fields=["current_version", "state", "updated_at"])
    if not actions.updates_task(action):
        return
    task = action.task
    task.status = GrowthTask.Status.AWAITING_APPROVAL
    task.result_payload = {k: v for k, v in (task.result_payload or {}).items() if k != "skipped"}
    task.save(update_fields=["status", "result_payload", "updated_at"])


def edit(program: GrowthProgram, public_id: str, request: EditRequest) -> GrowthAction:
    """Save the owner's edit as the next version, or raise ``DecisionRefused``."""
    with transaction.atomic():
        action = _locked_action(program, public_id)
        if action.state not in EDITABLE_STATES:
            raise DecisionRefused("illegal_state", f"This action is {action.state} and can no longer change.")
        if not matches_current(action, request):
            raise DecisionRefused("stale", "The draft changed since you opened it. Review it again.")
        current = action.current_version
        payload = edited_payload(action, request.fields)
        payload_hash = actions.content_hash(action.kind, action.connector, payload, current.destination)
        if payload_hash == current.payload_hash:
            return action  # nothing changed, so the approval state stands
        version = ActionVersion.objects.create(
            action=action,
            number=current.number + 1,
            payload=payload,
            destination=current.destination,
            payload_hash=payload_hash,
        )
        _back_to_review(action, version)
        audit.record(
            program,
            action.task,
            AUDIT_EDITED,
            AgentActionLog.Outcome.SUCCEEDED,
            {"action": action.public_id, "version": version.number, "from_version": current.number},
            actor_email=request.actor_email,
        )
    return action
