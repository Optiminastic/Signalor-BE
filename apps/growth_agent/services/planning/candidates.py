"""
Turn a positioning report into scored task candidates. Pure: no DB, no LLM.

Score = 100 x tactic weight x reach x confidence / effort, where reach is how
much of the brand's tracked AI visibility the target touches (0..1). Every
candidate keeps the evidence it came from, so the plan is explainable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .tactics import ENTITY_TACTICS, SOURCE_TACTICS, TACTICS, Tactic

SCORE_SCALE = 100
MAX_TOPIC_TASKS = 5
MAX_COMPARISON_TASKS = 3
TOPIC_REACH = 0.6  # topics come from answers that skip the brand; reach is moderate
ENTITY_REACH = 0.7
GAP_REACH_FLOOR = 0.3
LOST_PROMPT_REACH_FLOOR = 0.5
ENGINE_LABELS = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini", "perplexity": "Perplexity"}


@dataclass(frozen=True)
class TaskSpec:
    tactic: str
    channel: str
    title: str
    rationale: str
    target: str
    target_url: str
    score: float
    effort: int
    earliest_week: int
    prompts: tuple[str, ...] = field(default_factory=tuple)


def _engines_text(engines: list[str]) -> str:
    return ", ".join(ENGINE_LABELS.get(e, e.title()) for e in engines) or "AI engines"


def _spec(
    tactic: Tactic,
    *,
    target: str,
    reach: float,
    target_url: str = "",
    prompts: tuple[str, ...] = (),
    prompt_count: int = 0,
    engines: tuple[str, ...] | list[str] = (),
) -> TaskSpec:
    count = prompt_count or len(prompts)
    fmt = {"target": target, "prompts": count or "several", "engines": _engines_text(list(engines))}
    score = SCORE_SCALE * tactic.weight * max(0.0, min(reach, 1.0)) * tactic.confidence / tactic.effort
    return TaskSpec(
        tactic=tactic.key,
        channel=tactic.channel,
        title=tactic.title.format(**fmt),
        rationale=tactic.rationale.format(**fmt),
        target=target,
        target_url=target_url,
        score=round(score, 1),
        effort=tactic.effort,
        earliest_week=tactic.earliest_week,
        prompts=tuple(prompts),
    )


def _gap_slots(gap: dict) -> int:
    return int(gap.get("prompts", 0)) * len(gap.get("engines", []))


def gap_candidates(citation_gaps: list[dict]) -> list[TaskSpec]:
    """One task per cited gap site. Reach is relative to the strongest gap
    (prompts x engines it is cited for), floored so every listed gap still counts."""
    strongest = max((_gap_slots(g) for g in citation_gaps), default=0) or 1
    specs = []
    for gap in citation_gaps:
        tactic = TACTICS.get(SOURCE_TACTICS.get(gap.get("source_type", ""), ""))
        if tactic is None:
            continue
        reach = GAP_REACH_FLOOR + (1 - GAP_REACH_FLOOR) * _gap_slots(gap) / strongest
        examples = gap.get("example_urls") or []
        specs.append(
            _spec(
                tactic,
                target=gap["domain"],
                reach=reach,
                target_url=examples[0] if examples else f"https://{gap['domain']}",
                prompt_count=int(gap.get("prompts", 0)),
                engines=gap.get("engines", []),
            )
        )
    return specs


def topic_candidates(missing_topics: list[str]) -> list[TaskSpec]:
    tactic = TACTICS["answer_content"]
    return [_spec(tactic, target=topic, reach=TOPIC_REACH) for topic in missing_topics[:MAX_TOPIC_TASKS]]


def lost_prompt_candidates(lost_prompts: list[dict], engines_tracked: int) -> list[TaskSpec]:
    tactic = TACTICS["comparison_page"]
    specs = []
    for lost in lost_prompts[:MAX_COMPARISON_TASKS]:
        engines = sorted({e for r in lost.get("winning_rivals", []) for e in r.get("engines", [])})
        # A lost prompt is a direct loss to a competitor, so it starts at the floor
        # and grows with the number of engines the competitor wins it on.
        reach = LOST_PROMPT_REACH_FLOOR + (1 - LOST_PROMPT_REACH_FLOOR) * len(engines) / max(
            1, engines_tracked
        )
        specs.append(
            _spec(tactic, target=lost["prompt"], reach=reach, prompts=(lost["prompt"],), engines=engines)
        )
    return specs


def entity_candidates(entity_gaps: list[dict]) -> list[TaskSpec]:
    specs = []
    for gap in entity_gaps:
        tactic = TACTICS.get(ENTITY_TACTICS.get(gap.get("signal", ""), ""))
        if tactic is not None:
            specs.append(_spec(tactic, target="", reach=ENTITY_REACH))
    return specs


def _dedupe(specs: list[TaskSpec]) -> list[TaskSpec]:
    """Keep the highest-scoring spec per (tactic, target)."""
    best: dict[tuple[str, str], TaskSpec] = {}
    for spec in specs:
        key = (spec.tactic, spec.target.lower())
        if key not in best or spec.score > best[key].score:
            best[key] = spec
    return list(best.values())


def build_candidates(report: dict) -> list[TaskSpec]:
    """``report`` holds the PositioningReport JSON fields."""
    engines_tracked = len((report.get("share_of_voice") or {}).get("engines") or {}) or 1
    context = report.get("context_gaps") or {}
    specs = [
        *gap_candidates(report.get("citation_gaps") or []),
        *topic_candidates(context.get("missing_topics") or []),
        *lost_prompt_candidates(report.get("lost_prompts") or [], engines_tracked),
        *entity_candidates(report.get("entity_gaps") or []),
    ]
    return sorted(_dedupe(specs), key=lambda s: s.score, reverse=True)
