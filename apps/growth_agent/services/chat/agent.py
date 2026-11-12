"""
The Growth Agent chat: a bounded Sonnet tool loop over the brand's real data.

Each user message gets one assistant turn, run in the background. The model
must answer from tool results, may reshape the plan only through the narrow
write tools, and is told plainly what it must never do. Program state is
re-read from the database for every turn, so the chat never acts on stale data.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from ...models import AgentMessage, GrowthProgram
from .. import autopilot
from ..execution.brand import display_name
from . import tools
from .llm_tools import ask_llm_with_tools

logger = logging.getLogger("apps")
MAX_STEPS = 6
HISTORY_MESSAGES = 20
MAX_TOKENS = 1500
PURPOSE = "growth-chat"
FALLBACK_REPLY = "I could not finish that just now. Please try again in a moment."

SYSTEM_PROMPT = """You are the Signalor Growth Agent for {brand} ({url}). Today is {today}.
You run this brand's monthly SEO and GEO plan: pitching the sites AI engines cite (outreach emails and
their follow-ups), answer-first pages for the brand's site, on-site entity schema, and weekly
re-measurement of AI visibility.
Nothing goes out on its own. Every email and article is drafted first and waits for the owner's
approval in the Approvals tab on the board beside this chat; the owner can edit it, approve that
exact draft, reject it, or withdraw an approval before it is sent.
Program status: {status}. Setup progress: {stage}

If the owner asks to build or start a campaign while setup is still running, explain the setup
progress above in plain words: the plan is built automatically as soon as setup finishes, and its
drafts will then appear in Approvals. Never invent timelines such as billing cycles or dates that
are not in the setup progress or in a tool result.

How to answer:
- Use tools to look things up before answering. Every number you state must come from a tool result.
- If the data is not there, say so. Never guess or invent figures, customers or results.
- For claims about one AI engine, use only data for that engine: each citation gap lists the engines
  that cite it. Do not attribute a site to an engine that is not in its list.
- To change the plan, call the write tools; never claim a change you did not make.
- Keep replies short (under 150 words), plain language, bullets when listing. No markdown headings.

Hard limits (explain the reason if asked):
- No fake accounts, fake or paid reviews, or undisclosed posts. No automated Reddit or Quora posting:
  it gets brand accounts banned.
- G2/Capterra profiles and Wikipedia need a human to verify, so they are not in the plan.
- You cannot switch off brand-safety checks, daily caps or the monthly action budget.
"""


def _system(program: GrowthProgram) -> str:
    org = program.organization
    return SYSTEM_PROMPT.format(
        brand=display_name(org),
        url=org.url,
        today=timezone.now().date().isoformat(),
        status=program.status,
        stage=autopilot.onboarding_stage(program),
    )


def _history(reply: AgentMessage) -> list[dict]:
    """Recent finished messages of the same conversation (falls back to the program for old rows)."""
    scope = reply.conversation.messages if reply.conversation_id else reply.program.messages
    recent = (
        scope.filter(status=AgentMessage.Status.COMPLETE, id__lt=reply.id)
        .exclude(content="")
        .order_by("-created_at", "-id")[:HISTORY_MESSAGES]
    )
    return [{"role": m.role, "content": m.content} for m in reversed(list(recent))]


def run_loop(program: GrowthProgram, messages: list[dict]) -> tuple[str, list[dict]]:
    """Returns (reply, trace). ``messages`` starts with system + history."""
    trace: list[dict] = []
    for step in range(MAX_STEPS):
        out = ask_llm_with_tools(
            messages, tools.specs(), preferred_provider="sonnet", max_tokens=MAX_TOKENS, purpose=PURPOSE
        )
        if out["finish_reason"] in ("no_key", "error"):
            return FALLBACK_REPLY, trace
        if not out["tool_calls"]:
            return (out["text"] or FALLBACK_REPLY).strip(), trace
        messages.append(out["message"])
        for call in out["tool_calls"]:
            result = tools.dispatch(program, call["name"], call["arguments"])
            trace.append({"tool": call["name"], "write": call["name"] in tools.WRITE_TOOLS})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        if step == MAX_STEPS - 2:
            messages.append(
                {"role": "user", "content": "Answer the user now with what you have. No more tool calls."}
            )
    return FALLBACK_REPLY, trace


def _nudge_setup(program: GrowthProgram) -> None:
    """Take the next setup step if one is due, so the stage the agent reports is current."""
    try:
        autopilot.advance_onboarding(program)
        program.refresh_from_db()
    except Exception:  # setup trouble must never stop the chat from answering
        logger.exception("growth chat: could not advance setup for program %s", program.pk)


def run_turn(reply_id: int) -> None:
    reply = AgentMessage.objects.select_related("program__organization", "conversation").get(pk=reply_id)
    if reply.status != AgentMessage.Status.PENDING:
        return
    program = GrowthProgram.objects.select_related("organization").get(pk=reply.program_id)
    _nudge_setup(program)
    messages = [{"role": "system", "content": _system(program)}, *_history(reply)]
    try:
        text, trace = run_loop(program, messages)
        reply.status = AgentMessage.Status.COMPLETE
    except Exception:
        logger.exception("growth chat turn %s failed", reply_id)
        text, trace = FALLBACK_REPLY, []
        reply.status = AgentMessage.Status.FAILED
    reply.content, reply.tool_trace = text, trace
    reply.save(update_fields=["content", "tool_trace", "status", "updated_at"])
