"""
"Broader context the brand lacks", read from real AI answers.

One structured LLM call over excerpts of answers where competitors appear and
the brand does not. Fail-soft: on any LLM failure the report still ships with
its deterministic metrics and an empty context section.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.llm.structured import ask_structured

from .inputs import PositioningInputs
from .metrics import match_answers

MAX_EXCERPTS = 8
EXCERPT_CHARS = 700
MAX_TOKENS = 1500
PURPOSE = "growth-positioning-context"
MAX_RECOMMENDED_BRANDS = 10
MAX_TOPICS = 8
EMPTY_ATTRIBUTE_MARKERS = ("not mentioned", "no information", "n/a")


class RivalAttribute(BaseModel):
    rival: str = Field(description="Competitor name exactly as given")
    attribute: str = Field(description="What AI answers credit this competitor with, in a few words")


class ContextGaps(BaseModel):
    summary: str = Field(description="2 to 3 sentences on why AI engines pick competitors over the brand")
    ai_recommended_brands: list[str] = Field(
        default_factory=list,
        description="Products or companies the answers actually recommend, most frequent first, excluding the brand",
    )
    missing_topics: list[str] = Field(
        default_factory=list, description="Topics the answers cover that the brand is absent from"
    )
    rival_attributes: list[RivalAttribute] = Field(default_factory=list)
    source_types_ai_trusts: list[str] = Field(
        default_factory=list,
        description="Kinds of sources the answers lean on, e.g. review sites, Reddit, listicles",
    )


def _excerpts(inputs: PositioningInputs) -> list[str]:
    """Answers that leave the brand out, known-rival answers first, one per prompt."""
    candidates = [i for i in match_answers(inputs) if not i.brand and i.answer.response_text.strip()]
    candidates.sort(key=lambda i: len(i.rivals), reverse=True)
    picked, seen_prompts = [], set()
    for item in candidates:
        if item.answer.prompt_id in seen_prompts:
            continue
        seen_prompts.add(item.answer.prompt_id)
        text = " ".join(item.answer.response_text.split())[:EXCERPT_CHARS]
        picked.append(f"[{item.answer.engine}] Q: {item.answer.prompt_text}\nA: {text}")
        if len(picked) >= MAX_EXCERPTS:
            break
    return picked


def build_prompt(inputs: PositioningInputs, excerpts: list[str]) -> str:
    brand = inputs.brand_names[0] if inputs.brand_names else inputs.brand_domain
    rivals = ", ".join(r.name for r in inputs.rivals) or "none listed"
    joined = "\n\n".join(excerpts)
    return (
        f"Brand: {brand} ({inputs.brand_domain})\n"
        f"Competitors: {rivals}\n\n"
        "Below are real AI-engine answers to buyer questions where the brand was NOT mentioned. "
        "Using ONLY these answers, explain what context the brand is missing and which brands "
        "the answers recommend instead.\n"
        "Do not invent facts that are not in the answers.\n\n"
        f"{joined}"
    )


def analyse_context(inputs: PositioningInputs, organization=None) -> dict:
    excerpts = _excerpts(inputs)
    if not excerpts:
        return {}
    result = ask_structured(
        build_prompt(inputs, excerpts),
        ContextGaps,
        tier="medium",
        max_tokens=MAX_TOKENS,
        purpose=PURPOSE,
        cache_org=organization,
    )
    return _tidy(result).model_dump() if result else {}


def _tidy(result: ContextGaps) -> ContextGaps:
    """Drop filler the model adds for rivals it did not see, and cap list lengths."""
    attributes = [
        a
        for a in result.rival_attributes
        if not any(m in a.attribute.lower() for m in EMPTY_ATTRIBUTE_MARKERS)
    ]
    return result.model_copy(
        update={
            "rival_attributes": attributes,
            "ai_recommended_brands": result.ai_recommended_brands[:MAX_RECOMMENDED_BRANDS],
            "missing_topics": result.missing_topics[:MAX_TOPICS],
        }
    )
