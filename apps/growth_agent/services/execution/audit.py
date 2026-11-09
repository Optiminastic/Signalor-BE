"""Append-only audit trail helper. The only writer of AgentActionLog rows."""

from __future__ import annotations

from ...models import AgentActionLog, GrowthProgram, GrowthTask

DETAIL_TEXT_LIMIT = 1000


def _trim(value):
    if isinstance(value, str):
        return value[:DETAIL_TEXT_LIMIT]
    if isinstance(value, list):
        return [_trim(v) for v in value]
    if isinstance(value, dict):
        return {k: _trim(v) for k, v in value.items()}
    return value


def record(
    program: GrowthProgram,
    task: GrowthTask | None,
    action: str,
    outcome: str,
    detail: dict | None = None,
) -> AgentActionLog:
    return AgentActionLog.objects.create(
        program=program,
        task=task,
        channel=task.channel if task else "",
        action=action,
        outcome=outcome,
        detail=_trim(detail or {}),
    )
