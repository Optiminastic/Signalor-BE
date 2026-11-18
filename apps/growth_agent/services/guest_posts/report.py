"""The Guest posts tab: every guest-post site in this month's plan and where it stands. No network."""

from __future__ import annotations

import os

from django.db.models import Prefetch

from ...models import AgentActionLog, GrowthProgram, GrowthTask, OutreachThread
from ..planning.plan import active_cycle
from .finder import FOUND, MONTHLY_CAP, STARTED, TACTIC

# Task status -> the stage shown to the owner (refined below by the pitch thread).
STAGES = {
    GrowthTask.Status.PLANNED: "to_pitch",
    GrowthTask.Status.READY: "to_pitch",
    GrowthTask.Status.AWAITING_APPROVAL: "pitch_waiting",
    GrowthTask.Status.EXECUTING: "pitching",
    GrowthTask.Status.DONE: "pitched",
    GrowthTask.Status.FAILED: "failed",
    GrowthTask.Status.SKIPPED: "skipped",
}


# Once a pitch is sent, its email thread says where it stands.
THREAD_STAGES = {
    OutreachThread.Status.SENT: "pitched",
    OutreachThread.Status.FOLLOWED_UP: "pitched",
    OutreachThread.Status.REPLIED: "replied",
    OutreachThread.Status.OPTED_OUT: "declined",
    OutreachThread.Status.CLOSED: "no_reply",
}


def _stage(task: GrowthTask, thread: OutreachThread | None) -> str:
    if thread is not None:
        return THREAD_STAGES.get(thread.status, "pitched")
    if task.status == GrowthTask.Status.AWAITING_APPROVAL and "needs_contact" in (task.result_payload or {}):
        return "needs_contact"
    return STAGES.get(task.status, "to_pitch")


def _pitch(thread: OutreachThread | None) -> dict | None:
    if thread is None:
        return None
    return {
        "sent_on": thread.created_at.date().isoformat(),
        "followups_sent": thread.followups_sent,
        "next_follow_up_on": thread.next_follow_up_on.isoformat() if thread.next_follow_up_on else None,
        "replied_at": thread.replied_at.isoformat() if thread.replied_at else None,
    }


def _row(task: GrowthTask) -> dict:
    guest = (task.draft_payload or {}).get("guest") or {}
    # Prefetched newest first; a task has at most one pitch thread.
    thread = next(iter(task.outreach_threads.all()), None)
    return {
        "task_id": task.id,
        "domain": task.target,
        "guidelines_url": guest.get("guidelines_url") or task.target_url,
        "source": guest.get("source", "search"),
        "topic": guest.get("topic", ""),
        "reason": task.rationale,
        "scheduled_for": task.scheduled_for.isoformat(),
        "stage": _stage(task, thread),
        "pitch": _pitch(thread),
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
    threads = Prefetch("outreach_threads", queryset=OutreachThread.objects.order_by("-created_at"))
    tasks = (
        list(cycle.tasks.filter(tactic=TACTIC).prefetch_related(threads).order_by("scheduled_for", "id"))
        if cycle
        else []
    )
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
