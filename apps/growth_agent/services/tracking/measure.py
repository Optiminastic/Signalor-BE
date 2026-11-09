"""
Pure weekly measurement: visibility now vs baseline, closed gaps, task wins.

Attribution is correlational: a task "likely contributed" when the site it
targeted is now cited in answers that mention the brand. Reports must word it
that way, never as proven cause.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..positioning.inputs import PositioningInputs, bare_domain
from ..positioning.metrics import match_answers, share_of_voice

DELTA_PRECISION = 1


@dataclass(frozen=True)
class TaskRef:
    id: int
    title: str
    target: str
    target_url: str


def brand_cited_domains(inputs: PositioningInputs) -> set[str]:
    """Domains cited in answers that now mention the brand."""
    domains: set[str] = set()
    for item in match_answers(inputs):
        if item.brand:
            domains.update(c.domain for c in item.answer.citations if c.domain)
    return domains


def _covers(cited: set[str], domain: str) -> bool:
    return any(c == domain or c.endswith(f".{domain}") for c in cited) if domain else False


def closed_gaps(baseline_gaps: list[dict], cited: set[str]) -> list[str]:
    """Baseline gap sites that are now cited in answers mentioning the brand."""
    return sorted(g["domain"] for g in baseline_gaps if _covers(cited, g.get("domain", "")))


def attribute_wins(done_tasks: list[TaskRef], cited: set[str]) -> list[dict]:
    wins = []
    for task in done_tasks:
        domain = bare_domain(task.target_url) or bare_domain(task.target)
        if _covers(cited, domain):
            wins.append(
                {"task_id": task.id, "title": task.title, "domain": domain, "claim": "likely contributed"}
            )
    return wins


def measure(
    inputs: PositioningInputs,
    *,
    baseline_share_of_voice: float,
    previous_share_of_voice: float | None,
    baseline_gaps: list[dict],
    done_tasks: list[TaskRef],
) -> dict:
    matched = match_answers(inputs)
    sov = share_of_voice(inputs, matched)
    overall = sov["overall"]
    brand_now = overall["brand"]
    cited = brand_cited_domains(inputs)
    return {
        "share_of_voice": brand_now,
        "engines": {engine: rates["brand"] for engine, rates in sov["engines"].items()},
        "rivals": overall["rivals"],
        "answers_checked": overall["answers"],
        "delta_vs_baseline": round(brand_now - baseline_share_of_voice, DELTA_PRECISION),
        "delta_vs_previous": (
            None
            if previous_share_of_voice is None
            else round(brand_now - previous_share_of_voice, DELTA_PRECISION)
        ),
        "closed_gaps": closed_gaps(baseline_gaps, cited),
        "wins": attribute_wins(done_tasks, cited),
    }
