"""
Per-prompt backlink-opportunity generator.

Asks the LLM to surface 10-15 real, currently-existing sites where the brand
can submit a listing, claim a profile, or earn a citation that's relevant to
the prompt's intent. Output is persisted as BacklinkOpportunity rows tied to
the prompt; subsequent loads read from the DB without re-prompting the LLM.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Iterable

from apps.analyzer.models import BacklinkOpportunity, PromptTrack
from apps.analyzer.pipeline.llm import ask_llm

logger = logging.getLogger("apps")

VALID_CATEGORIES = {c.value for c in BacklinkOpportunity.Category}


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # ```json ... ``` or ``` ... ```
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _build_prompt(
    prompt_text: str,
    brand_name: str,
    brand_url: str,
    cited_domains: Iterable[str],
) -> str:
    cited = ", ".join(sorted({d for d in cited_domains if d})) or "(none)"
    return f"""You are a backlink acquisition strategist helping a brand earn citations on the open web.

BRAND: {brand_name or '(no name)'}
BRAND SITE: {brand_url or '(unknown)'}
USER QUERY: {prompt_text}
DOMAINS ALREADY CITED FOR THIS QUERY: {cited}

Generate 12 high-value, REAL, currently-existing sites where this brand can submit a listing, claim a profile, contribute content, or earn a citation. Choose targets that are:
- Topically relevant to the QUERY above
- Likely to be referenced by AI engines (ChatGPT, Claude, Gemini, Perplexity, Google AI Overviews) when answering similar questions
- Actually accepting submissions / listings / contributions today (not defunct sites)

Mix the categories so the user has options:
- "directory"  — industry directories, business listings, niche catalogs
- "review"     — review platforms (G2, Capterra, Trustpilot, Glassdoor, etc., as relevant)
- "press"      — journalism platforms (HARO, Qwoted, Help a B2B Writer, Source of Sources)
- "forum"      — communities (relevant subreddits, Quora topics, Indie Hackers, niche forums)
- "resource"   — "best X" / "top tools" lists where the brand could be added
- "other"      — anything that doesn't fit but is high value

Return ONLY a JSON array. No prose, no markdown fences, no explanation. Schema:
[
  {{
    "name": "Crunchbase",
    "description": "One-sentence description of what the site is and what listing/profile is offered.",
    "submit_url": "https://www.crunchbase.com/add-new",
    "category": "directory",
    "priority": 1,
    "rationale": "One sentence on why this site fits THIS brand and THIS query."
  }}
]

Priority: 1 = high value (cited often, easy to act on), 2 = medium, 3 = low.
URLs MUST be the actual submission/signup/contribution page if known, otherwise the homepage.
"""


def _parse_response(raw: str) -> list[dict]:
    cleaned = _strip_code_fences(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning("backlink_opportunities: JSON parse failed: %s; raw=%r", e, cleaned[:300])
        return []
    if not isinstance(data, list):
        logger.warning("backlink_opportunities: top-level not a list, got %s", type(data).__name__)
        return []

    out: list[dict] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        name = (row.get("name") or "").strip()
        url = (row.get("submit_url") or "").strip()
        if not name or not url:
            continue
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        category = (row.get("category") or "directory").strip().lower()
        if category not in VALID_CATEGORIES:
            category = "other"
        try:
            priority = int(row.get("priority") or 2)
        except (TypeError, ValueError):
            priority = 2
        priority = max(1, min(3, priority))
        out.append({
            "name": name[:200],
            "description": (row.get("description") or "").strip()[:400],
            "rationale": (row.get("rationale") or "").strip()[:400],
            "submit_url": url[:2048],
            "category": category,
            "priority": priority,
        })
    return out


def generate_for_prompt(track: PromptTrack) -> list[BacklinkOpportunity]:
    """
    Generate (or regenerate) backlink opportunities for a single PromptTrack.

    Persists newly-generated rows. Existing rows for the same track are NOT
    deleted — call delete_for_prompt() first if you want a clean slate.
    Returns the list of newly-created BacklinkOpportunity objects.
    """
    run = track.analysis_run
    cited = (
        track.results.values_list("citations__domain", flat=True)
        .exclude(citations__domain="")
        .distinct()
    )
    cited_domains = [d for d in cited if d]

    prompt = _build_prompt(
        prompt_text=track.prompt_text,
        brand_name=run.brand_name or "",
        brand_url=run.url or "",
        cited_domains=cited_domains,
    )

    raw = ask_llm(
        prompt,
        preferred_provider="gemini",
        max_tokens=2048,
        temperature=0.3,
        purpose=f"backlink_opportunities:track={track.pk}",
    )
    if not raw:
        logger.warning("backlink_opportunities: LLM returned empty for track %d", track.pk)
        return []

    rows = _parse_response(raw)
    if not rows:
        return []

    # De-dupe on (track, submit_url) so a regeneration doesn't multiply rows.
    existing_urls = set(
        BacklinkOpportunity.objects
        .filter(prompt_track=track)
        .values_list("submit_url", flat=True)
    )
    created: list[BacklinkOpportunity] = []
    for r in rows:
        if r["submit_url"] in existing_urls:
            continue
        created.append(BacklinkOpportunity.objects.create(
            prompt_track=track,
            name=r["name"],
            description=r["description"],
            rationale=r["rationale"],
            submit_url=r["submit_url"],
            category=r["category"],
            priority=r["priority"],
        ))
        existing_urls.add(r["submit_url"])
    return created


def delete_for_prompt(track: PromptTrack) -> int:
    """Hard-delete every opportunity for a track. Returns deleted count."""
    n, _ = BacklinkOpportunity.objects.filter(prompt_track=track).delete()
    return n
