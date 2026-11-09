"""
Tactic catalogue: every task the planner can create.

Only tactics the agent can carry out end to end with no human step are listed:
the monthly plan runs fully autonomously. Tactics that need a person (G2 or
Capterra vendor verification, Wikipedia, journalist platforms without APIs) or
that get accounts banned (automated Reddit/Quora posting) are deliberately
absent.

Weights encode the impact-first ranking (getting into pages AI already cites >
answer-first content > entity signals). ``earliest_week`` encodes sequencing:
foundations first, outreach once there is content to point at. Each tactic maps
to exactly one channel in ``models.CHANNELS``, the code-level allowlist.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tactic:
    key: str
    channel: str
    weight: float  # relative impact on AI visibility, 0..1
    effort: int  # 1 = light, 3 = heavy
    confidence: float  # how reliably this tactic lands, 0..1
    earliest_week: int
    title: str  # format string, receives ``target``
    rationale: str  # format string, receives ``target``, ``prompts``, ``engines``


TACTICS: dict[str, Tactic] = {
    t.key: t
    for t in (
        Tactic(
            key="listicle_inclusion",
            channel="outreach_email",
            weight=1.0,
            effort=2,
            confidence=0.6,
            earliest_week=2,
            title="Get listed on {target}",
            rationale="AI engines cite {target} for {prompts} of your prompts on {engines}, and you are not in it.",
        ),
        Tactic(
            key="publisher_pitch",
            channel="outreach_email",
            weight=0.75,
            effort=2,
            confidence=0.5,
            earliest_week=2,
            title="Pitch {target} for a mention",
            rationale="{target} is cited for {prompts} prompt(s) on {engines} in answers that leave you out.",
        ),
        Tactic(
            key="video_outreach",
            channel="outreach_email",
            weight=0.5,
            effort=2,
            confidence=0.4,
            earliest_week=3,
            title="Pitch creators on {target}",
            rationale="Videos on {target} are cited for {prompts} prompt(s) on {engines}.",
        ),
        Tactic(
            key="answer_content",
            channel="owned_content",
            weight=0.8,
            effort=2,
            confidence=0.8,
            earliest_week=1,
            title="Publish an answer-first page on: {target}",
            rationale="AI answers cover this topic and never mention you.",
        ),
        Tactic(
            key="comparison_page",
            channel="owned_content",
            weight=0.8,
            effort=2,
            confidence=0.8,
            earliest_week=1,
            title="Publish a comparison page for: {target}",
            rationale="Competitors win this question on {engines} and you are absent.",
        ),
        Tactic(
            key="entity_schema",
            channel="entity",
            weight=0.7,
            effort=1,
            confidence=0.9,
            earliest_week=1,
            title="Add Organization schema and sameAs links to your site",
            rationale="AI engines have no structured entity for the brand yet (no knowledge panel or Wikipedia page).",
        ),
    )
}

# Citation-gap source type -> tactic. Review sites, communities and reference
# sites are intentionally unmapped: none can be won without a human step.
SOURCE_TACTICS: dict[str, str] = {
    "listicle": "listicle_inclusion",
    "publisher": "publisher_pitch",
    "video": "video_outreach",
}

# Entity gap signal -> tactic. All entity gaps collapse into one schema task.
ENTITY_TACTICS: dict[str, str] = {
    "knowledge_panel": "entity_schema",
    "wikipedia": "entity_schema",
    "social": "entity_schema",
}
