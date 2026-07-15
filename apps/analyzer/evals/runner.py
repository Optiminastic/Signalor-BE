"""Prompt-evaluation runner (Epic 6).

Loads golden cases, renders each prompt through the registry, judges the output
(recorded known-good by default, or a live generation with ``live=True``), and persists a
``PromptEvalLog`` row per case. Returns structured results so the management command can
print a report and gate CI.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("apps")

_CASES_DIR = Path(__file__).parent / "cases"
_DEFAULT_THRESHOLDS = {"faithfulness": 0.7, "relevance": 0.7, "format_score": 0.7}


@dataclass
class CaseResult:
    case_id: str
    prompt: str
    version: str
    mode: str
    verdict: object | None  # judge.JudgeVerdict | None
    passed: bool
    tokens: dict = field(default_factory=dict)


def load_cases(*, prompt: str | None = None, version: str | None = None) -> list[dict]:
    """Load golden cases from ``cases/*.json``, optionally filtered by prompt/version."""
    cases: list[dict] = []
    for path in sorted(_CASES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if prompt and data.get("prompt") != prompt:
            continue
        if version and data.get("version") != version:
            continue
        cases.append(data)
    return cases


def run(
    *, prompt: str | None = None, version: str | None = None, live: bool = False, persist: bool = True
) -> list[CaseResult]:
    """Run all matching golden cases and return their results."""
    return [run_case(c, live=live, persist=persist) for c in load_cases(prompt=prompt, version=version)]


def run_case(case: dict, *, live: bool = False, persist: bool = True) -> CaseResult:
    from ..pipeline.judge import judge
    from ..prompts import current_version, render

    prompt_name = case["prompt"]
    ver = case.get("version") or current_version(prompt_name)
    task = render(prompt_name, version=ver, **case["vars"])
    mode = "live" if live else "recorded"

    if live:
        output, tokens = _live_generate(task)
    else:
        output, tokens = case.get("known_good", ""), {}

    verdict = judge(
        task=task,
        output=output,
        context=case.get("context"),
        reference=case.get("reference"),
        format_spec=case.get("format_spec"),
    )
    thresholds = {**_DEFAULT_THRESHOLDS, **(case.get("thresholds") or {})}
    result = CaseResult(
        case_id=case.get("id", ""),
        prompt=prompt_name,
        version=ver,
        mode=mode,
        verdict=verdict,
        passed=_meets(verdict, thresholds),
        tokens=tokens,
    )
    if persist:
        _persist(result)
    return result


def _meets(verdict, thresholds: dict) -> bool:
    if verdict is None:
        return False
    scores = verdict.scores
    return all(scores.get(axis, 0.0) >= floor for axis, floor in thresholds.items())


def _live_generate(task: str) -> tuple[str, dict]:
    """Generate output for a case live, capturing token usage from the LLM logs."""
    from ..pipeline.llm import ask_llm, get_collected_logs, start_log_collection

    start_log_collection()
    output = ask_llm(task, tier="cheap", purpose="eval_live", max_tokens=1024)
    tokens: dict = {}
    for entry in reversed(get_collected_logs() or []):
        if entry.get("usage"):
            tokens = entry["usage"]
            break
    return output, tokens


def _persist(result: CaseResult) -> None:
    from ..models import PromptEvalLog

    v = result.verdict
    PromptEvalLog.objects.create(
        prompt_name=result.prompt,
        prompt_version=result.version,
        case_id=result.case_id,
        mode=result.mode,
        faithfulness=getattr(v, "faithfulness", 0.0),
        relevance=getattr(v, "relevance", 0.0),
        format_score=getattr(v, "format_score", 0.0),
        passed=result.passed,
        rationale=getattr(v, "rationale", ""),
        prompt_tokens=result.tokens.get("prompt_tokens", 0),
        completion_tokens=result.tokens.get("completion_tokens", 0),
        total_tokens=result.tokens.get("total_tokens", 0),
    )
