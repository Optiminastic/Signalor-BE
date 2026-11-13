"""
Automatic guardrails checked before every autonomous action.

These replace human approval: the kill switch, program/cycle state, the
channel and tactic allowlists, per-channel daily caps and a per-cycle action
budget. All counts come from the audit log, so limits survive restarts.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils import timezone

from ...models import CHANNELS, AgentActionLog, GrowthCycle, GrowthProgram, GrowthTask
from ..planning.tactics import TACTICS

# Published actions per channel per day, per brand. Conservative on purpose:
# a burst of pages or emails looks like spam to search engines and inboxes.
DAILY_CHANNEL_CAPS: dict[str, int] = {
    "owned_content": 2,
    "outreach_email": 10,
    "entity": 2,
    "owned_social": 3,
    # One satellite article per brand per day at most (planned every 3 days).
    "satellite_content": 1,
}
DEFAULT_DAILY_CAP = 2
MAX_ACTIONS_PER_CYCLE = 40
COUNTED_OUTCOMES = (AgentActionLog.Outcome.SUCCEEDED, AgentActionLog.Outcome.DRY_RUN)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    retry_later: bool = False  # True when the block is temporary (caps), so the task waits


def _published(program: GrowthProgram, **filters) -> int:
    return AgentActionLog.objects.filter(
        program=program, outcome__in=COUNTED_OUTCOMES, action="publish", **filters
    ).count()


def check(task: GrowthTask) -> Decision:
    cycle = task.cycle
    program = cycle.program
    if program.kill_switch:
        return Decision(False, "Kill switch is on.")
    if program.status != GrowthProgram.Status.ACTIVE or cycle.status != GrowthCycle.Status.ACTIVE:
        return Decision(False, "Program or plan is not active.")
    if task.channel not in CHANNELS or task.tactic not in TACTICS:
        return Decision(False, f"'{task.tactic}' is not an autonomous tactic.")
    day_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    cap = DAILY_CHANNEL_CAPS.get(task.channel, DEFAULT_DAILY_CAP)
    if _published(program, channel=task.channel, created_at__gte=day_start) >= cap:
        return Decision(False, f"Daily cap of {cap} reached for {task.channel}.", retry_later=True)
    if _published(program, task__cycle=cycle) >= MAX_ACTIONS_PER_CYCLE:
        return Decision(False, f"Monthly budget of {MAX_ACTIONS_PER_CYCLE} actions used up.")
    return Decision(True)
