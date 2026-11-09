"""
Fit ranked candidates into a 4-week month. Pure: no DB.

Highest score first; each task lands in the first week at or after its
``earliest_week`` that still has capacity, then gets a weekday inside that
week so work is spread rather than bunched on Mondays.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .candidates import TaskSpec

WEEKS_PER_CYCLE = 4
TASKS_PER_WEEK = 6
MAX_TASKS_PER_CYCLE = WEEKS_PER_CYCLE * TASKS_PER_WEEK
DAYS_PER_WEEK = 7
WORKDAYS_PER_WEEK = 5


@dataclass(frozen=True)
class ScheduledTask:
    spec: TaskSpec
    week: int
    scheduled_for: date


def workday(week_start: date, index_in_week: int) -> date:
    """Spread tasks over the 5 workdays that follow ``week_start``."""
    day = week_start
    workdays_seen = -1
    while True:
        if day.weekday() < WORKDAYS_PER_WEEK:
            workdays_seen += 1
            if workdays_seen == index_in_week % WORKDAYS_PER_WEEK:
                return day
        day += timedelta(days=1)


def schedule(candidates: list[TaskSpec], start: date) -> list[ScheduledTask]:
    load = {week: 0 for week in range(1, WEEKS_PER_CYCLE + 1)}
    placed: list[ScheduledTask] = []
    for spec in sorted(candidates, key=lambda s: s.score, reverse=True):
        if len(placed) >= MAX_TASKS_PER_CYCLE:
            break
        week = next(
            (w for w in range(max(1, spec.earliest_week), WEEKS_PER_CYCLE + 1) if load[w] < TASKS_PER_WEEK),
            None,
        )
        if week is None:
            continue
        week_start = start + timedelta(days=(week - 1) * DAYS_PER_WEEK)
        placed.append(ScheduledTask(spec, week, workday(week_start, load[week])))
        load[week] += 1
    return sorted(placed, key=lambda t: (t.week, t.scheduled_for, -t.spec.score))
