"""The agent's weekly summary, posted as a chat conversation for the owner.

Built from what the agent already recorded (actions, the Agent log, indexing
checks, satellite articles), never by a model, so every number is exact and
nothing is claimed that didn't happen. One summary per brand per week, from
the brand's first full week; posted from ``run_growth_tasks``.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..models import AgentActionLog, AgentConversation, AgentMessage, GrowthAction, GrowthProgram, GrowthTask
from .execution import audit
from .indexing import bing, report, satellite_rank

POSTED = "weekly_summary_posted"
WEEK = timedelta(days=7)
PENDING_STATES = (GrowthAction.State.AWAITING_APPROVAL,)


def _since(program: GrowthProgram):
    return timezone.now() - WEEK


def _published(program: GrowthProgram, since) -> list[AgentActionLog]:
    logs = AgentActionLog.objects.filter(
        program=program, action="publish", outcome=AgentActionLog.Outcome.SUCCEEDED, created_at__gte=since
    )
    return list(logs.select_related("task"))


def _work_lines(program: GrowthProgram, since) -> list[str]:
    published = _published(program, since)
    waiting = GrowthAction.objects.filter(organization=program.organization, state__in=PENDING_STATES).count()
    needs_input = GrowthTask.objects.filter(
        cycle__program=program, status=GrowthTask.Status.AWAITING_APPROVAL, actions__isnull=True
    ).count()
    lines = [f"- **{len(published)}** sent or published this week"]
    lines += [f"  - {log.task.title}" for log in published[:5] if log.task]
    lines.append(f"- **{waiting}** drafts waiting for your approval")
    if needs_input:
        lines.append(f"- **{needs_input}** tasks need your input (for example a contact to write to)")
    return lines


def _indexing_lines(program: GrowthProgram) -> list[str]:
    built = report.build(program.organization)
    if built["status"] in ("not_connected", "no_property"):
        return ["- Search Console isn't connected yet, so Google indexing isn't tracked."]
    if not built["checked_at"]:
        return ["- The first Search Console check hasn't finished yet."]
    change = built["indexed_change"]
    delta = f" ({change:+d} since last week)" if change else ""
    lines = [f"- **{built['indexed']} of {built['checked']}** pages are on Google{delta}"]
    if built["to_request"]:
        lines.append(
            f"- {len(built['to_request'])} pages are ready to request in Search Console (Indexing tab)"
        )
    if built["reasons"]:
        top = built["reasons"][0]
        lines.append(f"- Most common reason pages aren't indexed: {top['label']} ({top['count']})")
    return lines


def _bing_line(program: GrowthProgram) -> list[str]:
    state = bing.state(program)
    last = state["last"]
    if not state["enabled"] or not last:
        return []
    if last["kind"] == "submitted":
        return [f"- Sent **{last['submitted']}** new or changed pages to Bing"]
    if last["kind"] == "blocked":
        return [f"- Couldn't send pages to Bing: {last['message']}"]
    return ["- No new or changed pages to send to Bing"]


def _satellite_lines(program: GrowthProgram) -> list[str]:
    rows = satellite_rank.articles(program)["articles"]
    if not rows:
        return []
    on_google = sum(1 for r in rows if (r["check"] or {}).get("indexed"))
    lagging = sum(1 for r in rows if r["flag"])
    lines = [f"- **{len(rows)}** articles on signalor.ai sites, **{on_google}** on Google so far"]
    if lagging:
        lines.append(f"- {lagging} need attention (see the Indexing tab)")
    return lines


def _next_lines(program: GrowthProgram) -> list[str]:
    today = timezone.now().date()
    upcoming = GrowthTask.objects.filter(
        cycle__program=program,
        status=GrowthTask.Status.PLANNED,
        scheduled_for__gte=today,
        scheduled_for__lt=today + WEEK,
    ).order_by("scheduled_for")
    count = upcoming.count()
    first = upcoming.first()
    if not count:
        return ["- Nothing new is scheduled for the coming week."]
    return [f"- **{count}** tasks scheduled, starting with: {first.title}"]


def build(program: GrowthProgram) -> str:
    """The summary text: ``## `` headings, **bold** and ``- `` bullets (what the chat renders)."""
    since = _since(program)
    sections = [
        ("This week", _work_lines(program, since)),
        ("Google and Bing", _indexing_lines(program) + _bing_line(program)),
        ("Articles on signalor.ai sites", _satellite_lines(program)),
        ("Coming up", _next_lines(program)),
    ]
    parts = [f"Here's your week for **{program.organization.name}**."]
    for title, lines in sections:
        if lines:
            parts += ["", f"## {title}", *lines]
    return "\n".join(parts)


def due(program: GrowthProgram) -> bool:
    """Active, at least a week old, and no summary in the last week."""
    now = timezone.now()
    if program.status != GrowthProgram.Status.ACTIVE or now - program.created_at < WEEK:
        return False
    return not AgentActionLog.objects.filter(
        program=program, action=POSTED, created_at__gte=now - WEEK
    ).exists()


def post(program: GrowthProgram) -> AgentConversation | None:
    """Post this week's summary as its own conversation, once a week."""
    if not due(program):
        return None
    text = build(program)
    title = f"Weekly summary · {timezone.now().strftime('%d %b')}"
    with transaction.atomic():
        conversation = AgentConversation.objects.create(program=program, title=title)
        AgentMessage.objects.create(
            program=program, conversation=conversation, role=AgentMessage.Role.ASSISTANT, content=text
        )
        audit.record(
            program, None, POSTED, AgentActionLog.Outcome.SUCCEEDED, {"conversation": conversation.public_id}
        )
    return conversation
