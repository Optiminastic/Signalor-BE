"""
Plan-editing chat tools. Narrow on purpose: the agent can reshape the month
(skip, restore, move, pause, change the goal) but cannot add tactics outside
the autonomous catalogue or switch off guardrails. Every successful edit is
written to the audit log. Task ids are always resolved inside the brand's own
active plan, so the model cannot touch another brand's data.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction

from ...models import CHANNELS, AgentActionLog, GrowthCycle, GrowthProgram, GrowthTask
from ..execution import audit
from ..planning.plan import USER_EDITABLE_STATUSES, active_cycle
from ..planning.schedule import DAYS_PER_WEEK, WEEKS_PER_CYCLE, workday

MAX_TASKS_PER_EDIT = 25
MIN_UPLIFT_POINTS = 1
MAX_UPLIFT_POINTS = 50
MAX_VISIBILITY = 100.0


def _log(program: GrowthProgram, action: str, detail: dict) -> None:
    audit.record(program, None, action, AgentActionLog.Outcome.SUCCEEDED, detail)


def _cycle_or_error(program: GrowthProgram) -> tuple[GrowthCycle | None, dict | None]:
    cycle = active_cycle(program)
    return (cycle, None) if cycle else (None, {"error": "There is no active month plan."})


def _task_ids(args: dict) -> list[int]:
    raw = args.get("task_ids") or []
    if not isinstance(raw, list):
        return []
    return [int(i) for i in raw[:MAX_TASKS_PER_EDIT] if str(i).isdigit()]


def _set_status(program: GrowthProgram, args: dict, status: str, action: str) -> dict:
    cycle, error = _cycle_or_error(program)
    if error:
        return error
    ids = _task_ids(args)
    changed = cycle.tasks.filter(pk__in=ids, status__in=USER_EDITABLE_STATUSES).update(status=status)
    if changed:
        _log(program, action, {"task_ids": ids})
    return {"updated": changed, "note": "Tasks already running or done cannot change."}


def skip_tasks(program: GrowthProgram, args: dict) -> dict:
    return _set_status(program, args, GrowthTask.Status.SKIPPED, "chat_skip_tasks")


def restore_tasks(program: GrowthProgram, args: dict) -> dict:
    return _set_status(program, args, GrowthTask.Status.PLANNED, "chat_restore_tasks")


def _channel_bulk(program: GrowthProgram, args: dict, from_status: str, to_status: str, action: str) -> dict:
    channel = str(args.get("channel", ""))
    if channel not in CHANNELS:
        return {"error": f"Unknown channel. Valid: {', '.join(CHANNELS)}"}
    cycle, error = _cycle_or_error(program)
    if error:
        return error
    changed = cycle.tasks.filter(channel=channel, status=from_status).update(status=to_status)
    if changed:
        _log(program, action, {"channel": channel, "tasks": changed})
    return {"updated": changed}


def skip_channel(program: GrowthProgram, args: dict) -> dict:
    return _channel_bulk(
        program, args, GrowthTask.Status.PLANNED, GrowthTask.Status.SKIPPED, "chat_skip_channel"
    )


def restore_channel(program: GrowthProgram, args: dict) -> dict:
    return _channel_bulk(
        program, args, GrowthTask.Status.SKIPPED, GrowthTask.Status.PLANNED, "chat_restore_channel"
    )


def move_task(program: GrowthProgram, args: dict) -> dict:
    cycle, error = _cycle_or_error(program)
    if error:
        return error
    week = args.get("week")
    if not isinstance(week, int) or not 1 <= week <= WEEKS_PER_CYCLE:
        return {"error": f"week must be 1 to {WEEKS_PER_CYCLE}"}
    task = cycle.tasks.filter(pk=args.get("task_id"), status=GrowthTask.Status.PLANNED).first()
    if task is None:
        return {"error": "Task not found or already started."}
    week_start = cycle.starts_on + timedelta(days=(week - 1) * DAYS_PER_WEEK)
    task.week, task.scheduled_for = week, workday(week_start, 0)
    task.save(update_fields=["week", "scheduled_for", "updated_at"])
    _log(program, "chat_move_task", {"task_id": task.id, "week": week})
    return {"moved": task.id, "week": week, "date": task.scheduled_for.isoformat()}


def _set_program_status(program: GrowthProgram, allowed_from: str, to: str, action: str) -> dict:
    if program.status != allowed_from:
        return {"error": f"Program is {program.status}, cannot change to {to}."}
    program.status = to
    program.save(update_fields=["status", "updated_at"])
    _log(program, action, {})
    return {"program_status": to}


def pause_program(program: GrowthProgram, _args: dict) -> dict:
    return _set_program_status(
        program, GrowthProgram.Status.ACTIVE, GrowthProgram.Status.PAUSED, "chat_pause"
    )


def resume_program(program: GrowthProgram, _args: dict) -> dict:
    return _set_program_status(
        program, GrowthProgram.Status.PAUSED, GrowthProgram.Status.ACTIVE, "chat_resume"
    )


def set_uplift_target(program: GrowthProgram, args: dict) -> dict:
    points = args.get("points")
    if not isinstance(points, int) or not MIN_UPLIFT_POINTS <= points <= MAX_UPLIFT_POINTS:
        return {"error": f"points must be {MIN_UPLIFT_POINTS} to {MAX_UPLIFT_POINTS}"}
    with transaction.atomic():
        program.uplift_target_points = points
        program.save(update_fields=["uplift_target_points", "updated_at"])
        cycle = active_cycle(program)
        if cycle:
            cycle.target_share_of_voice = min(MAX_VISIBILITY, cycle.baseline_share_of_voice + points)
            cycle.save(update_fields=["target_share_of_voice", "updated_at"])
    _log(program, "chat_set_target", {"points": points})
    return {
        "uplift_target_points": points,
        "target_visibility_pct": cycle.target_share_of_voice if cycle else None,
    }
