"""
Writes an answer-first page for an owned-content task.

Grounding: brand facts come only from the approved brand card and the brand's
own knowledge base (pgvector retrieval). The prompt forbids inventing numbers,
customers or claims about competitors; ``safety.py`` then checks the result.
"""

from __future__ import annotations

from core.llm.structured import ask_structured

from ...models import GrowthTask
from .article import ArticleDraft
from .brand import brand_card, brand_facts, display_name

WRITER_TIER = "strong"
WRITER_MAX_TOKENS = 4000
KNOWLEDGE_CHARS = 3000
PURPOSE = "growth-owned-content"

_TACTIC_BRIEFS = {
    "answer_content": (
        "Write an answer-first explainer on the topic below. Open with a direct 2 to 3 sentence answer, "
        "then 4 to 6 sections that cover the sub-questions buyers ask, then 4 to 6 FAQs."
    ),
    "comparison_page": (
        "Write a neutral, genuinely useful comparison guide that answers the buyer question below. "
        "Explain the evaluation criteria, where the brand fits and who it is best for. Mention other "
        "options only in neutral, verifiable terms; never disparage competitors."
    ),
}

_RULES = (
    "Rules:\n"
    "- Use ONLY facts about the brand from BRAND CONTEXT and KNOWLEDGE. If a fact is not there, leave it out.\n"
    "- Never invent statistics, prices, customer names, awards, rankings or testimonials.\n"
    "- Never make claims about competitors' pricing, quality or failings.\n"
    "- Write for humans first: specific, plain language, no hype words like 'revolutionary' or 'best-in-class'.\n"
    "- Each section should be self-contained so an AI engine can quote it on its own.\n"
    "- Include one section on where the brand fits, named after the brand, using only the facts given. "
    "If the facts are thin, keep it to a short, factual description of what the brand does."
)


def build_prompt(task: GrowthTask, brand_name: str, knowledge: str) -> str:
    brief = _TACTIC_BRIEFS.get(task.tactic, _TACTIC_BRIEFS["answer_content"])
    return (
        f"Brand: {brand_name}\n{brief}\n\nTOPIC / QUESTION: {task.target}\n\n"
        f"KNOWLEDGE (from the brand's own site):\n{knowledge or '(none available)'}\n\n{_RULES}"
    )


def gather_knowledge(task: GrowthTask) -> str:
    """Brand facts for this topic; the safety check must see exactly the same text."""
    return brand_facts(task.cycle.program.organization, task.target, KNOWLEDGE_CHARS)


def write_article(task: GrowthTask, knowledge: str) -> ArticleDraft | None:
    org = task.cycle.program.organization
    return ask_structured(
        build_prompt(task, display_name(org), knowledge),
        ArticleDraft,
        system=brand_card(org) or None,
        tier=WRITER_TIER,
        max_tokens=WRITER_MAX_TOKENS,
        temperature=0.3,
        purpose=PURPOSE,
    )
