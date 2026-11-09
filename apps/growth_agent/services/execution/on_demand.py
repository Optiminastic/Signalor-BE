"""
On-demand task actions: preview a task now, or pull its scheduled run forward.

Preview runs the channel's real work (writing, contact discovery, the full
brand-safety gate) in dry-run mode: nothing is published or sent, the task
keeps its place in the schedule, and it does not count toward daily caps
(audited as ``preview``, not ``publish``). No platform connection is needed.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from ...models import AgentActionLog, ChannelMode, GrowthProgram, GrowthTask
from . import audit
from .channels import REGISTRY

logger = logging.getLogger("apps")
PREVIEWABLE_STATUSES = (GrowthTask.Status.PLANNED, GrowthTask.Status.SKIPPED)


class OnDemandError(Exception):
    """The action can't run; the message is safe to show the user."""


def _set_preview(task: GrowthTask, preview: dict) -> None:
    task.result_payload = {**(task.result_payload or {}), "preview": preview}
    task.save(update_fields=["result_payload", "updated_at"])


def request_preview(task: GrowthTask) -> None:
    if task.channel not in REGISTRY:
        raise OnDemandError("This channel can't run yet.")
    if task.status not in PREVIEWABLE_STATUSES:
        raise OnDemandError("Only tasks that haven't started can be previewed.")
    if (task.result_payload or {}).get("preview", {}).get("status") == "running":
        raise OnDemandError("A preview is already running for this task.")
    _set_preview(task, {"status": "running", "started_at": timezone.now().isoformat()})


def run_preview(task_id: int) -> None:
    task = GrowthTask.objects.select_related("cycle__program__organization").get(pk=task_id)
    program = task.cycle.program
    try:
        outcome = REGISTRY[task.channel].run(task, ChannelMode.DRY_RUN)
    except Exception as exc:  # a crash must end the preview, not leave it "running"
        logger.exception("growth preview for task %s crashed", task_id)
        _set_preview(task, {"status": "failed", "error": str(exc)[:500]})
        audit.record(program, task, "preview", AgentActionLog.Outcome.FAILED, {"error": str(exc)})
        return
    if outcome.succeeded:
        _set_preview(task, {"status": "complete", **outcome.payload})
        audit.record(program, task, "preview", AgentActionLog.Outcome.DRY_RUN, outcome.payload)
        return
    detail = {"status": "failed", "error": outcome.reason, **outcome.payload}
    _set_preview(task, detail)
    audit.record(program, task, "preview", AgentActionLog.Outcome.FAILED, detail)


def schedule_now(task: GrowthTask) -> None:
    """Pull a planned task's scheduled day forward to today so it runs now."""
    if task.channel not in REGISTRY:
        raise OnDemandError("This channel can't run yet.")
    if task.status != GrowthTask.Status.PLANNED:
        raise OnDemandError("Only planned tasks can be run now.")
    if task.cycle.program.status != GrowthProgram.Status.ACTIVE:
        raise OnDemandError("The program is paused.")
    today = timezone.now().date()
    if task.scheduled_for > today:
        task.scheduled_for = today
        task.save(update_fields=["scheduled_for", "updated_at"])
