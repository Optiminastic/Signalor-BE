"""
Prompt tracking pipeline — fires a user-defined prompt across default AI engines
and returns per-engine mention/sentiment/rank data without touching the DB.

recheck_track() is the single entry-point used by both the management command
and the on-demand API endpoint.
"""
import logging

logger = logging.getLogger("apps")

# Maps internal provider keys → PromptResult.Engine choices
_ENGINE_MAP = {
    "gpt": "chatgpt",
    "claude": "claude",
    "gemini": "gemini",
}


def fire_prompt_across_engines(
    prompt_text: str,
    brand_name: str,
    brand_url: str,
) -> list[dict]:
    """
    Ask prompt_text to configured AI engines in parallel.

    Returns a list of dicts (one per engine), each containing:
      engine, response_text, brand_mentioned, sentiment, confidence, rank_position
    The caller is responsible for persisting these as PromptResult rows.
    """
    from .llm import ask_multiple_llms
    from .ai_visibility import _build_brand_aliases, _match_brand, _analyze_mention_quality, _check_ranking_position

    brand_aliases = _build_brand_aliases(brand_name, brand_url)

    try:
        responses = ask_multiple_llms(
            prompt_text,
            providers=["gpt", "claude", "gemini"],
            purpose="Prompt Track",
            max_tokens=512,
        )
    except Exception as exc:
        logger.warning("fire_prompt_across_engines failed: %s", exc)
        return []

    results = []
    for provider_key, response_text in responses.items():
        engine = _ENGINE_MAP.get(provider_key, provider_key)

        if not response_text:
            results.append({
                "engine": engine,
                "response_text": "",
                "brand_mentioned": False,
                "sentiment": "neutral",
                "confidence": 0.0,
                "rank_position": 0,
            })
            continue

        found, confidence, _ = _match_brand(brand_aliases, response_text)

        sentiment = "neutral"
        rank_position = 0
        if found:
            quality = _analyze_mention_quality(response_text, brand_aliases)
            sentiment = quality.get("sentiment", "neutral")
            ranking = _check_ranking_position(response_text, brand_aliases)
            rank_position = ranking.get("rank_position", 0)

        results.append({
            "engine": engine,
            "response_text": response_text[:3000],
            "brand_mentioned": found,
            "sentiment": sentiment,
            "confidence": round(confidence, 3),
            "rank_position": rank_position,
        })

    return results


def recheck_track(track, brand_name: str, brand_url: str) -> int:
    """
    Re-fire a PromptTrack across all configured engines and save a new set of
    PromptResult rows (each run appends — old rows are kept for history).

    Returns the number of new PromptResult rows created.
    """
    from django.db import close_old_connections
    from apps.analyzer.models import PromptResult

    close_old_connections()
    engine_results = fire_prompt_across_engines(track.prompt_text, brand_name, brand_url)
    created = 0
    for r in engine_results:
        PromptResult.objects.create(prompt_track=track, **r)
        created += 1
    logger.info(
        "recheck_track #%d ('%s'): %d new results",
        track.pk, track.prompt_text[:60], created,
    )
    return created


