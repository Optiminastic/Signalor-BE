"""
Brand-safety gate that replaces human review for autonomous publishing.

Two layers, both must pass:
1. Deterministic rules (pure, unit-tested): structure, length, banned hype and
   guarantee phrases, no URLs the model made up.
2. An independent LLM fact check against the brand's own knowledge: flags
   invented numbers, customers, awards or claims about competitors.

Anything that fails is never published; the task fails with the reasons.
If the fact check itself is unavailable, publishing is blocked (fail closed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from core.llm.structured import ask_structured

from .article import ArticleDraft, render_markdown, word_count

MIN_WORDS = 400
MAX_WORDS = 3500
MIN_SECTIONS = 3
META_MAX_CHARS = 160
CHECK_TIER = "medium"
CHECK_MAX_TOKENS = 800
PURPOSE = "growth-brand-safety"

BANNED_PHRASES = (
    "guaranteed",
    "guarantee you",
    "100% accurate",
    "best in the world",
    "number one",
    "#1 ",
    "revolutionary",
    "risk-free",
)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)


@dataclass(frozen=True)
class SafetyVerdict:
    passed: bool
    issues: tuple[str, ...]


class FactCheck(BaseModel):
    passed: bool = Field(description="True only if every brand claim is supported by the knowledge")
    issues: list[str] = Field(default_factory=list, description="Each unsupported or risky claim, quoted")


def rule_issues(article: ArticleDraft) -> list[str]:
    issues = []
    words = word_count(article)
    if words < MIN_WORDS:
        issues.append(f"Too short ({words} words, need {MIN_WORDS}).")
    if words > MAX_WORDS:
        issues.append(f"Too long ({words} words, max {MAX_WORDS}).")
    if len(article.sections) < MIN_SECTIONS:
        issues.append(f"Needs at least {MIN_SECTIONS} sections.")
    if not article.answer.strip():
        issues.append("Missing the answer-first opening.")
    if len(article.meta_description) > META_MAX_CHARS:
        issues.append("Meta description is over 160 characters.")
    text = render_markdown(article, {}).lower()
    issues += [f"Banned phrase: '{p.strip()}'." for p in BANNED_PHRASES if p in text]
    if _URL.search(text):
        issues.append("Contains URLs; links are not allowed in generated copy.")
    return issues


def _fact_check_prompt(text: str, brand_name: str, knowledge: str) -> str:
    return (
        f"You are a strict fact checker for {brand_name}. Compare the TEXT with the KNOWLEDGE.\n"
        "Fail the text if it states anything about the brand that the knowledge does not support "
        "(numbers, prices, customers, awards, integrations, rankings) or makes claims about competitors' "
        "pricing, quality or failings. General industry explanations are fine.\n\n"
        f"KNOWLEDGE:\n{knowledge or '(none)'}\n\nTEXT:\n{text}"
    )


def fact_check_text(text: str, brand_name: str, knowledge: str) -> SafetyVerdict:
    """Independent LLM fact check of any outgoing copy. Fails closed."""
    verdict = ask_structured(
        _fact_check_prompt(text, brand_name, knowledge),
        FactCheck,
        tier=CHECK_TIER,
        max_tokens=CHECK_MAX_TOKENS,
        purpose=PURPOSE,
    )
    if verdict is None:
        return SafetyVerdict(False, ("Fact check unavailable; not sending without it.",))
    if not verdict.passed:
        return SafetyVerdict(False, tuple(verdict.issues) or ("Fact check failed.",))
    return SafetyVerdict(True, ())


def check_article(article: ArticleDraft, brand_name: str, knowledge: str) -> SafetyVerdict:
    issues = rule_issues(article)
    if brand_name and brand_name.lower() not in render_markdown(article, {}).lower():
        issues.append(f"Page never mentions {brand_name}; it would not build the brand's visibility.")
    if issues:
        return SafetyVerdict(False, tuple(issues))
    return fact_check_text(render_markdown(article, {}), brand_name, knowledge)
