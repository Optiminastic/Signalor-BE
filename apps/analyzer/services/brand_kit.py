"""
Brand Submission Kit generator.

For a given workspace (AnalysisRun), produce a ready-to-paste set of brand
fields the user can drop into directory / review / press submission forms:

    - name                  (legal/display brand name)
    - url                   (canonical homepage)
    - tagline               (<60 chars — "company tagline" fields)
    - short_description     (~100 chars — short bio / meta fields)
    - long_description      (~300 chars — "about us" fields)
    - categories            (3–6 tag-like strings)
    - keywords              (5–10 short keywords for SEO/category fields)
    - location              (country if we have it)
    - contact_email         (run.email if available)

The kit is generated once and cached for 24 hours per workspace. A caller
can force a fresh generation by passing ``force=True``.

Why this exists: most users abandon backlink campaigns because filling out
each directory's submission form is tedious. The kit pre-composes 80% of
what they'd type, so submissions take seconds instead of minutes.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from django.core.cache import cache

from apps.analyzer.models import AnalysisRun
from apps.analyzer.pipeline.llm import ask_llm

logger = logging.getLogger("apps")

CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours
CACHE_KEY_PREFIX = "brandkit"


class BrandKitError(Exception):
    """Raised when the kit can't be generated (LLM unavailable, etc.)."""


def get_or_generate(run: AnalysisRun, *, force: bool = False) -> dict[str, Any]:
    """
    Return the persisted kit, or generate + persist one. Raises
    ``BrandKitError`` if generation fails AND no saved kit exists.
    """
    from apps.analyzer.models import BrandKit

    if not force:
        existing = BrandKit.objects.filter(analysis_run=run).first()
        if existing and existing.payload:
            return existing.payload

    raw = _ask_llm_for_kit(run)
    parsed = _parse_kit_response(raw)
    if not parsed:
        raise BrandKitError("LLM returned an unparseable kit response.")

    kit = _normalize_kit(parsed, run)
    BrandKit.objects.update_or_create(
        analysis_run=run, defaults={"payload": kit}
    )
    return kit


def invalidate(slug: str) -> None:
    """Drop the persisted kit for a workspace."""
    if not slug:
        return
    from apps.analyzer.models import AnalysisRun, BrandKit
    try:
        BrandKit.objects.filter(analysis_run__slug=slug).delete()
    except Exception:
        logger.warning("brand_kit invalidate failed for %s", slug, exc_info=True)


# ── Internals ────────────────────────────────────────────────────────────────

def _build_prompt(run: AnalysisRun) -> str:
    return f"""You are a brand-marketing copywriter producing a SUBMISSION KIT for directory and review-site listings.

BRAND NAME: {run.brand_name or '(unknown — infer from URL)'}
BRAND URL: {run.url or '(unknown)'}
COUNTRY: {run.country or '(unknown)'}

Produce a JSON object with these EXACT fields:

{{
  "name": "Display brand name (concise, no marketing fluff)",
  "tagline": "Single line under 60 characters. No period at the end.",
  "short_description": "100 characters max. One sentence describing the offering.",
  "long_description": "300 characters max. Two short sentences. Concrete value prop, no buzzwords.",
  "categories": ["3 to 6 industry-standard categories matching common directory dropdowns"],
  "keywords": ["5 to 10 short keywords or tags useful for SEO/category fields"],
  "location": "City, Country if knowable from URL/name, else just country, else empty string"
}}

Rules:
- Use REAL information you can infer from the brand URL — do not invent specific facts (founders, dates, revenue numbers).
- If a field is unknowable, use an empty string for strings, empty array for lists.
- Respect the character limits strictly.
- Return ONLY the JSON. No prose, no markdown fences.
"""


def _ask_llm_for_kit(run: AnalysisRun) -> str:
    raw = ask_llm(
        _build_prompt(run),
        preferred_provider="gemini",
        max_tokens=1024,
        temperature=0.4,
        purpose=f"brand_kit:run={run.pk}",
    )
    if not raw:
        raise BrandKitError("LLM returned empty response.")
    return raw


def _parse_kit_response(raw: str) -> dict | None:
    text = raw.strip()
    # Strip ```json ... ``` fences if the model added them.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("brand_kit JSON parse failed: %s; raw=%r", exc, text[:300])
        return None
    return data if isinstance(data, dict) else None


def _normalize_kit(data: dict, run: AnalysisRun) -> dict[str, Any]:
    """
    Trim, length-cap, and back-fill from the run for fields the LLM left blank.
    Always returns a complete dict so the frontend doesn't have to defend
    against missing keys.
    """
    name = (data.get("name") or run.brand_name or "").strip()
    url = (run.url or "").strip()

    return {
        "name": name[:120],
        "url": url[:2048],
        "tagline": (data.get("tagline") or "").strip()[:80],
        "short_description": (data.get("short_description") or "").strip()[:140],
        "long_description": (data.get("long_description") or "").strip()[:400],
        "categories": _clean_str_list(data.get("categories"), max_items=6, max_len=60),
        "keywords": _clean_str_list(data.get("keywords"), max_items=10, max_len=40),
        "location": (data.get("location") or run.country or "").strip()[:120],
        "contact_email": (run.email or "").strip(),
    }


def _clean_str_list(value, *, max_items: int, max_len: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for v in value:
        if not isinstance(v, str):
            continue
        s = v.strip()
        if not s:
            continue
        s = s[:max_len]
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _safe_cache_get(key: str):
    try:
        return cache.get(key)
    except Exception:
        logger.warning("brand_kit cache.get failed", exc_info=True)
        return None


def _safe_cache_set(key: str, value, ttl: int) -> None:
    try:
        cache.set(key, value, ttl)
    except Exception:
        logger.warning("brand_kit cache.set failed", exc_info=True)
