"""Central task → model routing for LLM calls.

One place to see and change which model each LLM task uses, instead of the choice
being scattered across call sites as inline ``preferred_provider=`` / ``tier=``
arguments. Each task resolves to a ``MODELS`` nickname (see ``pipeline/llm.py``),
env-overridable so a model can be A/B-swapped without a code change.

Cost-optimization workflow: benchmark a candidate model with the judge/eval harness
(``pipeline/judge.py`` + ``evals/runner.py``); if it passes at parity, flip the task's
model here (or via its env var). The ``FALLBACK_STRONG`` model is what a cheap-first
call escalates to when its output fails validation/judge.

IMPORTANT: the defaults below MUST mirror the current per-call-site choices, so
wiring a call site to read from this table is a behavior-preserving refactor. Do NOT
change a default here without the judge gate — that is the whole point of the table.
"""

from __future__ import annotations

import os

# task key -> (env var, current default nickname). Defaults mirror today's call sites.
_ROUTES: dict[str, tuple[str, str]] = {
    # GitHub fix agent (remediation/services/agent.py) — the cost target named in
    # this table's own note, now taken: DeepSeek V4 Flash instead of Sonnet, at
    # roughly 1/30th the input and 1/80th the output price. It is a tool-calling
    # loop over read-only repo tools, and every patch it proposes is gated by the
    # pure validators in fixers.py plus human PR review, so a weaker model costs
    # a rejected patch, not a bad merge. Revert instantly with
    # LLM_MODEL_FIX_AGENT=sonnet — no deploy needed.
    "fix_agent": ("LLM_MODEL_FIX_AGENT", "deepseek-v4"),
    # Blog long-form draft — today "opus" (analyzer/views.py:405). Cost target: cheaper.
    "blog_draft": ("LLM_MODEL_BLOG_DRAFT", "opus"),
    # Blog title ideas — today "opus" (analyzer/views.py:7298). Cost target: a cheap model.
    "blog_titles": ("LLM_MODEL_BLOG_TITLES", "opus"),
    # Analyzer auto-fix content/schema — today tier "medium" = haiku (auto_fix.py:326,341).
    "auto_fix_content": ("LLM_MODEL_AUTOFIX_CONTENT", "claude"),
    # Analyzer auto-fix meta / llms.txt — today tier "cheap" = gemini (auto_fix.py:370,387).
    "auto_fix_meta": ("LLM_MODEL_AUTOFIX_META", "gemini"),
    # Competitor selection (pipeline/competitors.py). Deliberately NOT mirroring the
    # previous call sites: this task ran on "gemini" (cheap tier) with a dead web
    # search behind it, so it answered from training data and invented companies.
    # The search is now grounded in Serper; "sonnet" is here because picking five
    # direct competitors out of ~24 real search results is a judgment task, not an
    # extraction task. Drop to a cheaper nickname via the env var if the judge gate
    # shows parity.
    "competitor_discovery": ("LLM_MODEL_COMPETITOR_DISCOVERY", "sonnet"),
}

# Strong model a cheap-first call escalates to when its output fails validation/judge.
FALLBACK_STRONG = os.getenv("LLM_MODEL_FALLBACK_STRONG", "sonnet")


def model_for(task: str, default: str = "gemini") -> str:
    """Return the ``MODELS`` nickname for a task key (env-overridable).

    Unknown tasks fall back to ``default``. Callers pass the result as
    ``preferred_provider=`` to ``ask_llm`` / ``ask_llm_with_tools``; an unknown
    nickname is itself handled safely by ``llm._pick_model`` (round-robin default).
    """
    env_var, fallback = _ROUTES.get(task, ("", default))
    if not env_var:
        return default
    return os.getenv(env_var) or fallback
