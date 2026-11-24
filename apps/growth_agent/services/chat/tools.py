"""
Tool registry for the chat agent: OpenAI-style schemas plus dispatch.

Only names listed here can ever run; unknown names and handler crashes come
back to the model as an error string instead of breaking the turn.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from ...models import CHANNELS, GrowthProgram
from . import insight_tools, read_tools, status_tools, write_tools

logger = logging.getLogger("apps")
Handler = Callable[[GrowthProgram, dict], dict]
_NO_ARGS = {"type": "object", "properties": {}}
_TASK_IDS = {
    "type": "object",
    "properties": {"task_ids": {"type": "array", "items": {"type": "integer"}}},
    "required": ["task_ids"],
}
_CHANNEL = {
    "type": "object",
    "properties": {"channel": {"type": "string", "enum": list(CHANNELS)}},
    "required": ["channel"],
}

_TOOLS: dict[str, tuple[str, dict, Handler]] = {
    "get_overview": (
        "Start here for any status or 'how is it going' question: plan task counts by tactic and status, "
        "drafts waiting for the owner, sends with unknown outcome, outreach replies, guest-post sites, "
        "social score, and which tools are connected.",
        _NO_ARGS,
        status_tools.get_overview,
    ),
    "get_approvals": (
        "Drafts waiting for the owner's approval (emails, articles, social posts) with a preview, and what "
        "happened to recent ones (sent, failed, rejected, outcome unknown).",
        _NO_ARGS,
        status_tools.get_approvals,
    ),
    "get_outreach": (
        "Outreach email threads: which sites replied, opted out, are waiting, and upcoming follow-ups.",
        _NO_ARGS,
        status_tools.get_outreach,
    ),
    "get_guest_posts": (
        "Sites that invite guest articles, found by the agent: why each was picked and its stage.",
        _NO_ARGS,
        status_tools.get_guest_posts,
    ),
    "get_competitor_tracking": (
        "The brand vs its top competitors, this week vs last: share of AI answers mentioning each (GEO), "
        "keywords in Google's top 10 and each tracked keyword's position (SEO), and what the internet "
        "said about each this week (news, blogs, Reddit) with sentiment. Use for 'how are my competitors "
        "doing', 'who ranks for X', 'what are people saying about us', 'are we improving'.",
        _NO_ARGS,
        insight_tools.get_competitor_tracking,
    ),
    "get_keywords": (
        "Keyword data: keywords the brand ranks for, keywords competitors win where the brand doesn't "
        "(with monthly searches), and the brand's top Search Console queries.",
        _NO_ARGS,
        insight_tools.get_keywords,
    ),
    "get_site_health": (
        "The daily site check (up or down, noindex, robots.txt blocking Google or AI bots, sitemap, homepage "
        "title), anything that changed on the site in the last 30 days including Google clicks drops, and the "
        "latest technical checks (GPTBot, PerplexityBot and ClaudeBot allowed in robots.txt; sitemap found, "
        "broken URLs, redirects, freshness), each passed, failed or unknown.",
        _NO_ARGS,
        insight_tools.get_site_health,
    ),
    "check_ranks_now": (
        "Start a check of Google positions and AI Overview citations for the tracked keywords (once a day).",
        _NO_ARGS,
        insight_tools.check_ranks_now,
    ),
    "refresh_keywords_now": (
        "Start a fresh read of the brand's keywords and competitor keyword gaps (once a day).",
        _NO_ARGS,
        insight_tools.refresh_keywords_now,
    ),
    "get_connections": (
        "Which outside tools are connected (Gmail, Hunter, Apollo, Buffer, Search Console, Bing, "
        "WordPress/GitHub publishing), what each enables, and where to connect it.",
        _NO_ARGS,
        status_tools.get_connections,
    ),
    "get_positioning": (
        "Where the brand stands in AI answers: visibility per engine, cited gap sites, lost prompts, "
        "brands AI recommends instead, missing topics.",
        _NO_ARGS,
        read_tools.get_positioning,
    ),
    "get_plan": (
        "The active month plan with every task (id, week, channel, status).",
        _NO_ARGS,
        read_tools.get_plan,
    ),
    "get_progress": ("Weekly visibility snapshots vs baseline and goal.", _NO_ARGS, read_tools.get_progress),
    "get_activity": (
        "What the agent recently did, with outcomes and reasons.",
        _NO_ARGS,
        read_tools.get_activity,
    ),
    "get_indexing": (
        "Google indexing (pages on Google, reasons, pages to request), weekly Bing sending, and the "
        "brand's articles on signalor.ai's satellite sites (planned topics, published, on Google or not).",
        _NO_ARGS,
        read_tools.get_indexing,
    ),
    "get_social": (
        "The brand's social presence score: per platform (LinkedIn, X, YouTube, Instagram, Reddit) and "
        "overall, what each is based on, what isn't measured, and the change since last week.",
        _NO_ARGS,
        read_tools.get_social,
    ),
    "search_brand_knowledge": (
        "Search the brand's own site content for facts.",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        read_tools.search_brand_knowledge,
    ),
    "skip_tasks": ("Skip planned tasks by id.", _TASK_IDS, write_tools.skip_tasks),
    "restore_tasks": ("Restore skipped tasks by id.", _TASK_IDS, write_tools.restore_tasks),
    "skip_channel": ("Skip every planned task in one channel.", _CHANNEL, write_tools.skip_channel),
    "restore_channel": ("Restore every skipped task in one channel.", _CHANNEL, write_tools.restore_channel),
    "move_task": (
        "Move one planned task to another week of the month (1-4).",
        {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "week": {"type": "integer", "minimum": 1, "maximum": 4},
            },
            "required": ["task_id", "week"],
        },
        write_tools.move_task,
    ),
    "find_guest_sites": (
        "Search for more sites that invite guest articles now (max every 6 hours).",
        _NO_ARGS,
        write_tools.find_guest_sites,
    ),
    "check_social_now": (
        "Re-measure the social presence score now (max every 6 hours).",
        _NO_ARGS,
        write_tools.check_social_now,
    ),
    "check_indexing_now": (
        "Run a Google Search Console indexing check now.",
        _NO_ARGS,
        write_tools.check_indexing_now,
    ),
    "pause_program": ("Pause all autonomous work for this brand.", _NO_ARGS, write_tools.pause_program),
    "resume_program": ("Resume autonomous work after a pause.", _NO_ARGS, write_tools.resume_program),
    "set_uplift_target": (
        "Change the monthly AI-visibility goal, in percentage points above the baseline.",
        {"type": "object", "properties": {"points": {"type": "integer"}}, "required": ["points"]},
        write_tools.set_uplift_target,
    ),
}

WRITE_TOOLS = frozenset(
    name for name, (_, _, handler) in _TOOLS.items() if handler.__module__.endswith("write_tools")
)


def specs() -> list[dict]:
    return [
        {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}
        for name, (desc, params, _) in _TOOLS.items()
    ]


def dispatch(program: GrowthProgram, name: str, args: dict) -> str:
    entry = _TOOLS.get(name)
    if entry is None:
        return json.dumps({"error": f"Unknown tool {name}."})
    try:
        result = entry[2](program, args if isinstance(args, dict) else {})
    except Exception:
        logger.exception("growth chat tool %s failed", name)
        result = {"error": "The tool failed. Tell the user and do not retry."}
    return json.dumps(result, default=str)
