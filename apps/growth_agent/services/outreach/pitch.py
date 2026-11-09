"""
Outreach pitch and follow-up copy.

The first email is written by the LLM from the brand's own knowledge and the
target page, then passes rule checks and the shared fact check. The sender
line and opt-out sentence are appended here, never left to the model, so
every email identifies the sender and offers a way out. Follow-ups are short
fixed templates: no LLM cost and nothing new to fact check.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.llm.structured import ask_structured

from ...models import GrowthTask
from ..execution.brand import brand_card, display_name
from ..execution.safety import BANNED_PHRASES, SafetyVerdict, fact_check_text

WRITER_TIER = "medium"
WRITER_MAX_TOKENS = 900
PURPOSE = "growth-outreach-pitch"
MIN_WORDS = 50
MAX_WORDS = 200
MAX_SUBJECT_CHARS = 80
OPT_OUT_LINE = 'Not relevant? Reply "no thanks" and I won\'t email again.'
OPT_OUT_WORDS = ("no thanks", "unsubscribe", "not interested", "remove me", "stop emailing", "don't email")

FOLLOW_UPS = (
    "Hi again, just bumping this in case it got buried. Happy to send anything that makes it easier "
    "to review {brand} for your piece.",
    "Last note from me on this. If {brand} isn't a fit for the article, no problem at all, and thanks "
    "for reading.",
)

_ASKS = {
    "listicle_inclusion": "ask whether they would consider including the brand in this list or roundup",
    "publisher_pitch": "offer a short expert comment or data point the writer could use, and ask for a mention",
    "video_outreach": "ask whether the creator would consider covering or mentioning the brand",
}


class PitchDraft(BaseModel):
    subject: str = Field(description="Specific, lowercase-friendly subject under 80 characters, no clickbait")
    body: str = Field(description="50 to 180 words, plain text, no links, signed with the brand name only")


def build_prompt(task: GrowthTask, page_url: str, knowledge: str) -> str:
    org = task.cycle.program.organization
    ask = _ASKS.get(task.tactic, _ASKS["publisher_pitch"])
    return (
        f"Write a short, personal outreach email from {display_name(org)} to the editor of {task.target}.\n"
        f"Their page AI engines cite: {page_url}\nGoal: {ask}.\n"
        "Show you understand what their page covers, explain in one or two sentences why the brand is "
        "relevant to their readers using ONLY the KNOWLEDGE, and make one clear, low-effort ask.\n"
        "No flattery, no hype, no links, no attachments, no invented facts.\n\n"
        f"KNOWLEDGE:\n{knowledge or '(none)'}"
    )


def write_pitch(task: GrowthTask, page_url: str, knowledge: str) -> PitchDraft | None:
    org = task.cycle.program.organization
    return ask_structured(
        build_prompt(task, page_url, knowledge),
        PitchDraft,
        system=brand_card(org) or None,
        tier=WRITER_TIER,
        max_tokens=WRITER_MAX_TOKENS,
        temperature=0.4,
        purpose=PURPOSE,
    )


def rule_issues(pitch: PitchDraft) -> list[str]:
    issues = []
    words = len(pitch.body.split())
    if not MIN_WORDS <= words <= MAX_WORDS:
        issues.append(f"Body must be {MIN_WORDS}-{MAX_WORDS} words (got {words}).")
    if not pitch.subject.strip() or len(pitch.subject) > MAX_SUBJECT_CHARS:
        issues.append("Subject missing or too long.")
    text = f"{pitch.subject} {pitch.body}".lower()
    issues += [f"Banned phrase: '{p.strip()}'." for p in BANNED_PHRASES if p in text]
    if "http://" in text or "https://" in text or "www." in text:
        issues.append("Links are not allowed in a first pitch.")
    return issues


def check_pitch(pitch: PitchDraft, brand_name: str, knowledge: str) -> SafetyVerdict:
    issues = rule_issues(pitch)
    if issues:
        return SafetyVerdict(False, tuple(issues))
    return fact_check_text(f"Subject: {pitch.subject}\n\n{pitch.body}", brand_name, knowledge)


def with_footer(body: str, sender_email: str, brand_name: str, brand_url: str) -> str:
    return f"{body.strip()}\n\n--\n{brand_name} ({brand_url}), sent by {sender_email}\n{OPT_OUT_LINE}"


def follow_up_body(number: int, brand_name: str) -> str:
    return FOLLOW_UPS[min(number, len(FOLLOW_UPS)) - 1].format(brand=brand_name)


def is_opt_out(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in OPT_OUT_WORDS)
