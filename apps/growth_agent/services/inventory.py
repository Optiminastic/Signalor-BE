"""
Read-only inventory of every Growth program, for the Phase 1 cutover decision.

Answers, per brand: how the program is configured today, what is in flight, and
what the cutover would have to handle (tasks stuck in ``executing``, outreach
threads with a follow-up due). It reports the brand's slug and never an email
address or a contact.

The number of queries is fixed, whatever the number of programs.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from django.db.models import Count, Q

from ..models import ChannelMode, GrowthCycle, GrowthProgram, GrowthTask, OutreachThread
from .outreach.followups import OPEN_STATUSES

QUERY_COUNT = 4  # programs, active cycles, tasks by status, open threads


@dataclass(frozen=True)
class ProgramInventory:
    org_slug: str
    status: str
    kill_switch: bool
    channel_modes: dict[str, str]
    autonomous_channels: list[str]
    active_cycle_id: int | None
    active_cycle_ends_on: date | None
    tasks_by_status: dict[str, int] = field(default_factory=dict)
    executing_tasks: int = 0
    open_threads: int = 0
    threads_follow_up_due: int = 0


def _active_cycles() -> dict[int, GrowthCycle]:
    """The newest active cycle per program, matching ``planning.active_cycle``."""
    newest: dict[int, GrowthCycle] = {}
    cycles = GrowthCycle.objects.filter(status=GrowthCycle.Status.ACTIVE).order_by("-created_at")
    for cycle in cycles:
        newest.setdefault(cycle.program_id, cycle)
    return newest


def _task_counts(cycle_ids: list[int]) -> dict[int, dict[str, int]]:
    counts: dict[int, dict[str, int]] = defaultdict(dict)
    rows = (
        GrowthTask.objects.filter(cycle_id__in=cycle_ids).values("cycle_id", "status").annotate(n=Count("id"))
    )
    for row in rows:
        counts[row["cycle_id"]][row["status"]] = row["n"]
    return counts


def _thread_counts(today: date) -> dict[int, dict[str, int]]:
    rows = (
        OutreachThread.objects.filter(status__in=OPEN_STATUSES)
        .values("task__cycle__program_id")
        .annotate(open=Count("id"), due=Count("id", filter=Q(next_follow_up_on__lte=today)))
    )
    return {row["task__cycle__program_id"]: {"open": row["open"], "due": row["due"]} for row in rows}


def collect(today: date) -> list[ProgramInventory]:
    programs = list(GrowthProgram.objects.select_related("organization").order_by("created_at"))
    cycles = _active_cycles()
    tasks = _task_counts([cycle.id for cycle in cycles.values()])
    threads = _thread_counts(today)

    entries = []
    for program in programs:
        cycle = cycles.get(program.id)
        by_status = dict(tasks.get(cycle.id, {})) if cycle else {}
        modes = dict(program.channel_modes or {})
        thread_counts = threads.get(program.id, {})
        entries.append(
            ProgramInventory(
                org_slug=program.organization.slug,
                status=program.status,
                kill_switch=program.kill_switch,
                channel_modes=modes,
                autonomous_channels=sorted(c for c, m in modes.items() if m == ChannelMode.AUTONOMOUS.value),
                active_cycle_id=cycle.id if cycle else None,
                active_cycle_ends_on=cycle.ends_on if cycle else None,
                tasks_by_status=by_status,
                executing_tasks=by_status.get(GrowthTask.Status.EXECUTING, 0),
                open_threads=thread_counts.get("open", 0),
                threads_follow_up_due=thread_counts.get("due", 0),
            )
        )
    return entries
