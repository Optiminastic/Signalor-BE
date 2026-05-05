"""
Site-level backlink-opportunity generator.

Sister module to `backlink_opportunities.py` (which is per-prompt). This one
returns AI-generated submission targets for the *brand as a whole*, with no
prompt context — used by the standalone Backlinks page where the user shouldn't
need to pick a prompt first.

Results are not persisted as ORM rows (no model is keyed to AnalysisRun for
opportunities). Instead the calling view caches the latest list inside
`BrandKit.payload` so reloads don't re-hit the LLM.
"""
from __future__ import annotations

import json
import logging
import re

from apps.analyzer.models import AnalysisRun
from apps.analyzer.pipeline.llm import ask_llm

logger = logging.getLogger("apps")

VALID_CATEGORIES = {"directory", "review", "press", "forum", "resource", "other"}


def _strip_code_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _build_prompt(brand_name: str, brand_url: str, brand_description: str = "") -> str:
    desc_block = f"\nWHAT THEY DO: {brand_description}" if brand_description else ""
    return f"""You are a backlink acquisition strategist helping a brand earn citations on the open web.

BRAND: {brand_name or '(no name)'}
BRAND SITE: {brand_url or '(unknown)'}{desc_block}

Generate 12 high-value, REAL, currently-existing sites where this brand can submit a listing, claim a profile, contribute content, or earn a citation. Choose targets that are:
- Topically relevant to the brand's industry / niche
- Likely to be referenced by AI engines (ChatGPT, Claude, Gemini, Perplexity, Google AI Overviews)
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
    "description": "One-sentence description of the site and what listing/profile is offered.",
    "submit_url": "https://www.crunchbase.com/add-new",
    "category": "directory",
    "priority": 1,
    "rationale": "One sentence on why this site fits THIS brand."
  }}
]

Priority: 1 = high value (cited often, easy to act on), 2 = medium, 3 = low.
URLs MUST be the actual submission/signup/contribution page if known, otherwise the homepage.
"""


def _parse_response(raw: str) -> list[dict]:
    cleaned = _strip_code_fences(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("site_backlink_opportunities: JSON parse failed: %s; raw=%r", exc, cleaned[:300])
        return []
    if not isinstance(data, list):
        logger.warning(
            "site_backlink_opportunities: top-level not a list, got %s",
            type(data).__name__,
        )
        return []

    out: list[dict] = []
    for idx, row in enumerate(data):
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
            "id": idx,
            "name": name[:200],
            "description": (row.get("description") or "").strip()[:400],
            "rationale": (row.get("rationale") or "").strip()[:400],
            "submit_url": url[:2048],
            "category": category,
            "priority": priority,
        })
    return out


def generate_for_run(run: AnalysisRun) -> list[dict]:
    """Ask the LLM for site-level opportunities. Returns plain dicts (no DB)."""
    prompt = _build_prompt(
        brand_name=run.brand_name or "",
        brand_url=run.url or "",
        brand_description=getattr(run, "brand_description", "") or "",
    )
    raw = ask_llm(
        prompt,
        preferred_provider="gemini",
        max_tokens=2048,
        temperature=0.3,
        purpose=f"site_backlink_opportunities:run={run.pk}",
    )
    if not raw:
        logger.warning("site_backlink_opportunities: LLM returned empty for run %d", run.pk)
        return []
    return _parse_response(raw)
