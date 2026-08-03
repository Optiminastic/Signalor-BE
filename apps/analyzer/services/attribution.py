"""What completing a task actually improves.

The dashboard used to show a task's name and nothing about *why* it is worth
doing, so every row looked equally (un)important. This maps a recommendation to
the signal it moves, in the user's language.

Deliberately honest about magnitude: this returns WHICH signal improves, not a
predicted score delta. The recommendation catalog already refuses to invent
impact numbers (see ``pipeline/recommendations.py``), and a made-up "+6 points"
next to every task would be worse than no number at all.

Pure module — no Django, no DB. Callers pass the finding's pillar and code.
"""

from __future__ import annotations

# pillar -> (short label, what improving it does for the brand)
_PILLAR_ATTRIBUTION = {
    "content": ("Content", "Makes the page easier for AI engines to quote."),
    "schema": ("Schema", "Helps engines parse what the page is about."),
    "eeat": ("E-E-A-T", "Builds the credibility engines look for before citing."),
    "technical": ("Technical", "Lets AI crawlers reach and read the page."),
    "entity": ("Entity", "Helps engines recognise the brand as a known entity."),
    "ai_visibility": ("AI visibility", "Affects whether the brand appears in AI answers."),
}
_FALLBACK = ("GEO", "Improves the brand's overall GEO score.")

# GEO-signal findings are not about a pillar score — they trace to a measured
# prompt or citation, so they get their own, more specific attribution.
_GEO_ATTRIBUTION = {
    "geo_prompt_lost": (
        "AI visibility",
        "Targets a tracked prompt the brand is currently absent from.",
    ),
    "geo_competitor_cited": (
        "AI visibility",
        "Closes a gap where a competitor is cited and the brand is not.",
    ),
    "geo_citation_gap": (
        "Off-site",
        "Targets a third-party source AI engines already cite for these prompts.",
    ),
    "geo_competitor_pillar_gap": (
        "Competitive",
        "Narrows the pillar gap against the strongest tracked competitor.",
    ),
}


# Codes whose evidence names the exact tracked prompt the task is for. Naming it
# beats any pillar label: "AI visibility" is a category, the prompt is the reason.
_PROMPT_ATTRIBUTED = frozenset({"geo_prompt_lost"})

# Long prompts wrap the row; the tail rarely carries the distinguishing words.
_PROMPT_LABEL_CHARS = 90


def _prompt_effect(evidence: dict | None) -> str:
    """"Targets the tracked prompt: …" when the evidence names one, else ""."""
    prompt = ((evidence or {}).get("prompt") or "").strip()
    if not prompt:
        return ""
    if len(prompt) > _PROMPT_LABEL_CHARS:
        prompt = prompt[: _PROMPT_LABEL_CHARS - 1].rstrip() + "…"
    return f"Targets the tracked prompt: \u201c{prompt}\u201d"


def attribution_for(
    pillar: str, finding_code: str = "", evidence: dict | None = None
) -> dict[str, str]:
    """``{"signal", "effect"}`` — what the task improves, and what that does.

    ``signal`` is a short badge label. ``effect`` is one plain sentence, safe to
    show under the task name. When the finding traces to a specific tracked
    prompt, ``effect`` names that prompt rather than its pillar.
    """
    code = (finding_code or "").strip().lower()
    if code in _PROMPT_ATTRIBUTED:
        effect = _prompt_effect(evidence)
        if effect:
            return {"signal": "Prompt", "effect": effect}
    if code in _GEO_ATTRIBUTION:
        signal, effect = _GEO_ATTRIBUTION[code]
        return {"signal": signal, "effect": effect}
    signal, effect = _PILLAR_ATTRIBUTION.get((pillar or "").strip().lower(), _FALLBACK)
    return {"signal": signal, "effect": effect}
