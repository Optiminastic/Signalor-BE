"""The Guest posts tab: every guest-post site in this month's plan and where it stands. No network."""

from __future__ import annotations

import os

from ...models import AgentActionLog, GrowthProgram, GrowthTask
from ..planning.plan import active_cycle
from .finder import FOUND, MONTHLY_CAP, STARTED, TACTIC

# Task status -> the stage shown to the owner. Pitching arrives in the next build step.
STAGES = {
    GrowthTask.Status.PLANNED: "to_pitch",
    GrowthTask.Status.READY: "to_pitch",
    GrowthTask.Status.AWAITING_APPROVAL: "pitch_waiting",
    GrowthTask.Status.EXECUTING: "pitching",
    GrowthTask.Status.DONE: "pitched",
    GrowthTask.Status.FAILED: "failed",
    GrowthTask.Status.SKIPPED: "skipped",
}


def _row(task: GrowthTask) -> dict:
    guest = (task.draft_payload or {}).get("guest") or {}
    return {
        "task_id": task.id,
        "domain": task.target,
        "guidelines_url": guest.get("guidelines_url") or task.target_url,
        "source": guest.get("source", "search"),
        "topic": guest.get("topic", ""),
        "reason": task.rationale,
        "scheduled_for": task.scheduled_for.isoformat(),
        "stage": STAGES.get(task.status, "to_pitch"),
    }


def _search_state(program: GrowthProgram) -> dict:
    found = AgentActionLog.objects.filter(program=program, action=FOUND).order_by("-created_at").first()
    started = AgentActionLog.objects.filter(program=program, action=STARTED).order_by("-created_at").first()
    searching = started is not None and (found is None or started.created_at > found.created_at)
    return {
        "searching": searching,
        "last_search_at": found.created_at.isoformat() if found else None,
        "last_found": (found.detail or {}).get("count", 0) if found else None,
    }


def report(program: GrowthProgram) -> dict:
    cycle = active_cycle(program)
    tasks = list(cycle.tasks.filter(tactic=TACTIC).order_by("scheduled_for", "id")) if cycle else []
    notes = []
    if cycle is None:
        notes.append("There is no active plan yet, so there is nowhere to add guest-post sites.")
    if not os.getenv("SERPER_API_KEY"):
        notes.append(
            "Web search isn't set up on the server, so only sites AI answers already cite are checked."
        )
    return {
        "sites": [_row(t) for t in tasks],
        "monthly_cap": MONTHLY_CAP,
        "notes": notes,
        **_search_state(program),
    }
