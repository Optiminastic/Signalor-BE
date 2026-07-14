"""
LLM-as-judge — the missing quality gate for the brand-context pipeline.

The RAG stack (retrieval + brand card + structured output) is built, but nothing
measures whether feeding that context actually produces *better* analysis. This
module scores a model output against the task it was asked to do and (optionally)
the source context it was given, so regressions in prompt/retrieval quality become
observable instead of silent.

Design (mirrors ``structured.py``):
- Pure module: no Django/DB imports, so it is ``SimpleTestCase``-friendly and can
  run in the eval harness or inline.
- The judge is itself a structured call — a ``JudgeVerdict`` Pydantic model — so its
  own output is validated, not string-parsed.
- Fail-soft: any failure returns ``None``; callers never break because the judge
  couldn't run.
- Judging costs a real LLM call, so it is **off by default** and **sampled**. Inline
  callers gate on :func:`should_judge`; the golden-eval command judges every row.
"""

from __future__ import annotations

import logging
import os
import random

from pydantic import BaseModel, Field

from .structured import ask_structured

logger = logging.getLogger("apps")

# Score band. 1 = unacceptable, 5 = excellent. A single scale keeps the judge
# prompt simple and the aggregate numbers comparable across criteria.
_MIN_SCORE = 1
_MAX_SCORE = 5

_JUDGE_SYSTEM = (
    "You are a strict, impartial evaluator of AI-generated SEO/GEO analysis output. "
    "Score only what is present. Never reward confident wording; reward correctness, "
    "grounding in the provided context, and usefulness. If the output makes a claim "
    "that the provided context does not support, that is a faithfulness failure."
)


class JudgeVerdict(BaseModel):
    """Validated evaluation of one model output. Scores are 1..5 (higher is better)."""

    relevance: int = Field(
        ge=_MIN_SCORE, le=_MAX_SCORE, description="Does the output actually address the task?"
    )
    faithfulness: int = Field(
        ge=_MIN_SCORE,
        le=_MAX_SCORE,
        description="Is every claim grounded in the provided context? No hallucinated facts.",
    )
    format_quality: int = Field(
        ge=_MIN_SCORE, le=_MAX_SCORE, description="Is it well-formed, specific, and actionable?"
    )
    passed: bool = Field(description="Overall: is this output acceptable to ship to a user?")
    notes: str = Field(default="", description="One or two sentences on the main weakness, if any.")

    @property
    def average(self) -> float:
        return round((self.relevance + self.faithfulness + self.format_quality) / 3, 2)


def _enabled() -> bool:
    """Master kill-switch for *inline* judging. Off by default — the golden-eval
    command judges regardless of this flag (it is an explicit, opt-in run)."""
    return os.getenv("LLM_JUDGE_ENABLED", "false").strip().lower() == "true"


def _sample_rate() -> float:
    """Fraction of inline traffic to judge, in [0, 1]. Default 5%."""
    try:
        rate = float(os.getenv("LLM_JUDGE_SAMPLE_RATE", "0.05"))
    except ValueError:
        return 0.05
    return min(1.0, max(0.0, rate))


def should_judge() -> bool:
    """Gate for inline callers: enabled AND wins the sampling dice.

    Keeps judging cost bounded on the request path; the eval command bypasses this.
    """
    if not _enabled():
        return False
    return random.random() < _sample_rate()


def _build_prompt(task: str, output: str, *, context: str = "", reference: str = "") -> str:
    parts = [
        "Evaluate the AI output below.",
        "",
        "### TASK (what the AI was asked to produce)",
        task.strip() or "(not provided)",
        "",
        "### AI OUTPUT (what it produced)",
        output.strip() or "(empty)",
    ]
    if context.strip():
        parts += [
            "",
            "### PROVIDED CONTEXT (the only source the AI was allowed to rely on)",
            context.strip()[:8000],
        ]
    if reference.strip():
        parts += [
            "",
            "### REFERENCE (a known-good answer for comparison)",
            reference.strip()[:4000],
        ]
    parts += [
        "",
        "Score relevance, faithfulness, and format_quality on a 1-5 scale "
        "(1=unacceptable, 5=excellent) and decide whether the output passes.",
    ]
    return "\n".join(parts)


def judge_output(
    task: str,
    output: str,
    *,
    context: str = "",
    reference: str = "",
    tier: str = "strong",
    purpose: str = "judge",
) -> JudgeVerdict | None:
    """Score a single model output. Returns a validated :class:`JudgeVerdict`, or
    ``None`` if the judge call/validation failed (fail-soft).

    ``context`` is the material the output was supposed to be grounded in (e.g. the
    retrieved knowledge block); supplying it enables real faithfulness scoring.
    ``reference`` is an optional golden answer for comparison.
    """
    if not output or not output.strip():
        return None
    prompt = _build_prompt(task, output, context=context, reference=reference)
    verdict = ask_structured(
        prompt,
        JudgeVerdict,
        system=_JUDGE_SYSTEM,
        tier=tier,
        temperature=0.0,
        purpose=purpose,
    )
    if verdict is None:
        logger.warning("judge_output[%s] produced no valid verdict", purpose)
    return verdict
