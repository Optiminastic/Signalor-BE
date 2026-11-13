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
from . import read_tools, write_tools

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
