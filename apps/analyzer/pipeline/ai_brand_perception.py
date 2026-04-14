"""Synthesize how LLMs are likely to characterize a brand from visibility signals only."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from apps.analyzer.pipeline.llm import ask_llm, is_available

logger = logging.getLogger("apps")

_MAX_JSON_CHARS = 5500


def _json_clip(obj: Any) -> str:
    try:
        s = json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:
        s = str(obj)
    s = s.replace("\x00", "")
    if len(s) > _MAX_JSON_CHARS:
        return s[:_MAX_JSON_CHARS] + "…"
    return s


def run_ai_brand_perception(
    brand_name: str,
    brand_url: str,
    google_details: dict,
    reddit_details: dict,
    medium_details: dict,
    web_mentions_details: dict,
    *,
    stored_brand_name: str = "",
) -> dict:
    """
    Ask the LLM to state what it would reasonably infer about the brand online,
    grounded ONLY in the provided signal bundle (no live browsing).
    """
    if not is_available():
        return {
            "facts": [],
            "summary": "",
            "caveat": "Connect an LLM (OpenRouter or Gemini) to generate AI perception notes.",
            "error": "no_llm",
        }

    bundle = {
        "primary_brand_label": brand_name,
        "brand_url": brand_url,
        "user_supplied_brand_name": (stored_brand_name or "").strip(),
        "google_visibility": google_details if isinstance(google_details, dict) else {},
        "reddit_visibility": reddit_details if isinstance(reddit_details, dict) else {},
        "medium_visibility": medium_details if isinstance(medium_details, dict) else {},
        "web_mentions": web_mentions_details if isinstance(web_mentions_details, dict) else {},
    }
    context = _json_clip(bundle)

    prompt = f"""You summarize how a chat LLM might talk about ONE website using ONLY the JSON below. No web search. No outside knowledge.

Identity: **brand_url** + **primary_brand_label** (from hostname). Ignore **user_supplied_brand_name** if it conflicts with the URL.

STRICT GROUNDING — violations make the answer useless:
- Do NOT name any social platform (Instagram, Facebook, X/Twitter, LinkedIn, YouTube, TikTok, Pinterest, etc.) unless that exact word OR its domain (e.g. instagram.com, facebook.com) appears inside the JSON string values (not in your head).
- Do NOT state indexed page counts, follower counts, revenue, awards, or rankings unless that exact number or claim appears in the JSON.
- Do NOT claim "strong presence", "well known", or "leader" unless the JSON explicitly supports it with concrete fields (e.g. scores, mention counts, snippets).
- Every fact must be a direct paraphrase of something inferable from a specific subsection: google_visibility, reddit_visibility, medium_visibility, or web_mentions. If a subsection is empty or only errors, say signals are missing for that channel.
- If the JSON is mostly empty or LLM-estimated, facts must say uncertainty is high and avoid specific platform lists.

Return ONLY valid JSON (no markdown):
{{
  "facts": ["string", ...],
  "summary": "string",
  "caveat": "string"
}}

- Max 5 facts; each ≤ 220 chars; no URLs unless they appear in the JSON.
- summary: 1-2 cautious sentences.
- caveat: one sentence on what was NOT in the data.

JSON:
{context}
"""

    try:
        raw = ask_llm(
            prompt,
            preferred_provider="gemini",
            max_tokens=900,
            temperature=0.0,
            purpose="AI brand perception (visibility)",
        )
        if not raw:
            return {
                "facts": [],
                "summary": "",
                "caveat": "Could not generate AI perception summary.",
                "error": "empty_llm_response",
            }

        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return {
                "facts": [],
                "summary": raw[:500].strip(),
                "caveat": "Response was not structured JSON; showing excerpt only.",
                "error": "parse_json",
            }
        data = json.loads(m.group())
        facts = data.get("facts") or []
        if not isinstance(facts, list):
            facts = []
        facts = [str(f).strip() for f in facts if str(f).strip()][:5]
        summary = str(data.get("summary") or "").strip()[:1200]
        caveat = str(data.get("caveat") or "").strip()[:500]
        return {
            "facts": facts,
            "summary": summary,
            "caveat": caveat,
            "method": "llm_visibility_signals",
        }
    except json.JSONDecodeError as exc:
        logger.warning("AI brand perception JSON parse failed: %s", exc)
        return {
            "facts": [],
            "summary": "",
            "caveat": "Could not parse model output.",
            "error": "json_decode",
        }
    except Exception as exc:
        logger.warning("AI brand perception failed: %s", exc)
        return {
            "facts": [],
            "summary": "",
            "caveat": str(exc)[:200],
            "error": "exception",
        }
