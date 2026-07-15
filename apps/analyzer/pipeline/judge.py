"""LLM-as-judge for prompt evaluation (Epic 6).

Scores an AI output on three axes - faithfulness, relevance, format - so prompt changes
become measurable. The judge prompt is itself a versioned registry template
(``judge_eval``); scoring runs on the STRONG tier because a weak judge is unreliable.

Fail-soft: ``judge`` returns ``None`` if the judge call fails, so an eval run records the
case as errored rather than crashing.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

logger = logging.getLogger("apps")

# passed=true requires every axis to clear this bar (mirrored in the judge template).
PASS_THRESHOLD = 0.7


class JudgeVerdict(BaseModel):
    faithfulness: float = 0.0
    relevance: float = 0.0
    format_score: float = 0.0
    passed: bool = False
    rationale: str = ""

    @property
    def scores(self) -> dict[str, float]:
        return {
            "faithfulness": self.faithfulness,
            "relevance": self.relevance,
            "format_score": self.format_score,
        }


def _clamp(value: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def judge(
    *,
    task: str,
    output: str,
    context: str | None = None,
    reference: str | None = None,
    format_spec: str | None = None,
    tier: str = "strong",
) -> JudgeVerdict | None:
    """Grade ``output`` against ``task`` (and optional context/reference/format).

    Returns a ``JudgeVerdict`` with 0-1 axis scores, or ``None`` if the judge call
    failed. Scores are clamped to [0, 1] and ``passed`` is re-derived from the
    threshold so it can't disagree with the numbers.
    """
    from ..prompts import render
    from .structured import ask_structured

    prompt = render(
        "judge_eval",
        task=task,
        output=output,
        context=context or "",
        reference=reference or "",
        format_spec=format_spec or "",
    )
    verdict = ask_structured(
        prompt,
        JudgeVerdict,
        tier=tier,
        purpose="judge_eval",
        max_tokens=500,
        temperature=0.0,
    )
    if verdict is None:
        logger.warning("judge: no verdict returned for task=%r", (task or "")[:60])
        return None

    verdict.faithfulness = _clamp(verdict.faithfulness)
    verdict.relevance = _clamp(verdict.relevance)
    verdict.format_score = _clamp(verdict.format_score)
    verdict.passed = all(s >= PASS_THRESHOLD for s in verdict.scores.values())
    return verdict
