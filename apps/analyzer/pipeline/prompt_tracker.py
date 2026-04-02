"""
Prompt ranking engine — fires prompts across AI engines multiple times,
extracts signals, computes weighted scores, and ranks prompts.

Inspired by Peec.ai's prompt ranking mechanism:
  Score = (Visibility × 0.5) + (Position Score × 0.3) + (Sentiment × 0.2)
"""
import json
import logging

logger = logging.getLogger("apps")

# Maps internal provider keys → PromptResult.Engine choices
_ENGINE_MAP = {
    "gpt": "chatgpt",
    "claude": "claude",
    "gemini": "gemini",
}

DEFAULT_RUNS = 3  # Fire each prompt N times to handle AI randomness


def _search_google_serper(query: str, brand_name: str, brand_url: str) -> dict:
    """
    Search Google via Serper.dev and check if brand appears in results.
    Free: 2,500 searches/month.

    Returns a PromptResult-compatible dict with engine="google".
    """
    import os
    import requests as _requests
    from urllib.parse import urlparse

    api_key = os.getenv("SERPER_API_KEY", "")
    if not api_key:
        return None

    try:
        resp = _requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": 10},
            timeout=10,
        )
        if not resp.ok:
            logger.warning("Serper search failed: %d", resp.status_code)
            return None

        data = resp.json()
    except Exception as exc:
        logger.warning("Serper search error: %s", exc)
        return None

    # Check organic results for brand mentions
    brand_lower = brand_name.lower()
    domain = urlparse(brand_url).netloc.lower().replace("www.", "")
    organic = data.get("organic", [])
    answer_box = data.get("answerBox", {})
    knowledge_graph = data.get("knowledgeGraph", {})

    mentioned = False
    rank_position = 0
    response_parts = []

    # Check answer box
    ab_text = (answer_box.get("snippet", "") or answer_box.get("answer", "")).lower()
    if brand_lower in ab_text or domain in ab_text:
        mentioned = True
        rank_position = 1
        response_parts.append(f"[Answer Box] {answer_box.get('snippet', answer_box.get('answer', ''))}")

    # Check knowledge graph
    kg_title = (knowledge_graph.get("title", "")).lower()
    kg_desc = (knowledge_graph.get("description", "")).lower()
    if brand_lower in kg_title or brand_lower in kg_desc or domain in kg_title:
        mentioned = True
        if rank_position == 0:
            rank_position = 1
        response_parts.append(f"[Knowledge Graph] {knowledge_graph.get('title', '')}: {knowledge_graph.get('description', '')}")

    # Check organic results
    for i, result in enumerate(organic[:10], 1):
        title = (result.get("title", "")).lower()
        snippet = (result.get("snippet", "")).lower()
        link = (result.get("link", "")).lower()
        result_domain = urlparse(link).netloc.lower().replace("www.", "")

        if brand_lower in title or brand_lower in snippet or domain == result_domain:
            mentioned = True
            if rank_position == 0:
                rank_position = i
            response_parts.append(f"[#{i}] {result.get('title', '')} — {result.get('snippet', '')}")
        else:
            response_parts.append(f"[#{i}] {result.get('title', '')}")

    # Determine sentiment from snippets
    sentiment = "neutral"
    if mentioned:
        all_snippets = " ".join(s.lower() for s in response_parts)
        pos_words = ["best", "top", "leading", "recommend", "popular", "trusted", "great", "excellent"]
        neg_words = ["worst", "avoid", "poor", "bad", "scam", "issue", "problem"]
        pos_count = sum(1 for w in pos_words if w in all_snippets)
        neg_count = sum(1 for w in neg_words if w in all_snippets)
        if pos_count > neg_count:
            sentiment = "positive"
        elif neg_count > pos_count:
            sentiment = "negative"

    return {
        "engine": "google",
        "response_text": "\n".join(response_parts[:5])[:3000],
        "brand_mentioned": mentioned,
        "sentiment": sentiment,
        "confidence": 1.0 if mentioned else 0.0,
        "rank_position": rank_position,
    }


def generate_brand_prompts(
    brand_name: str,
    brand_url: str,
    industry: str = "",
    page_content: str = "",
    meta_description: str = "",
    products: list[str] | None = None,
    location: str = "",
    country: str = "",
    count: int = 10,
) -> list[str]:
    """
    Use AI to deeply understand the brand — what it is (product, service, person,
    local business, anything) — then generate prompts real users would ask AI.
    """
    from .llm import ask_llm as ask_single_llm

    # Build rich context
    context_parts = [
        f"Brand/Entity Name: {brand_name}",
        f"Website: {brand_url}",
    ]
    if industry:
        context_parts.append(f"Industry detected: {industry}")
    if meta_description:
        context_parts.append(f"Meta description: {meta_description}")
    if location or country:
        loc = ", ".join(filter(None, [location, country]))
        context_parts.append(f"Location/Region: {loc}")
    if page_content:
        context_parts.append(f"Website content (first 2000 chars):\n{page_content[:2000]}")
    if products:
        context_parts.append(f"Pages/products found on site: {', '.join(products[:10])}")

    # Detect TLD for location hints
    from urllib.parse import urlparse
    try:
        tld = urlparse(brand_url).netloc.split(".")[-1].lower()
        tld_locations = {
            "in": "India", "uk": "United Kingdom", "au": "Australia", "ca": "Canada",
            "de": "Germany", "fr": "France", "jp": "Japan", "br": "Brazil",
            "sg": "Singapore", "ae": "UAE", "sa": "Saudi Arabia", "ng": "Nigeria",
        }
        if tld in tld_locations and not country:
            context_parts.append(f"TLD suggests location: {tld_locations[tld]}")
    except Exception:
        pass

    prompt = f"""You are a GEO (Generative Engine Optimization) expert. Your job is to deeply understand this entity and generate {count} prompts that real people would type into ChatGPT, Gemini, Perplexity, or Claude.

CONTEXT:
{chr(10).join(context_parts)}

STEP 1 — First, figure out WHAT this entity is:
- Is it a SaaS product? Physical product? Service? Agency? Local business? Blog? Personal brand? Marketplace? Nonprofit? Something else?
- What specific problem does it solve or what need does it serve?
- Who is the target audience (age, role, industry, location)?
- What are the key offerings (products, services, content)?
- Who are the likely competitors or alternatives?
- Is location relevant? (local business, regional service, global SaaS)

STEP 2 — Generate {count} prompts that REAL users would ask AI assistants. Cover these categories:

1. DISCOVERY (2 prompts): User searching for what this entity offers
   - Be specific to the actual niche, not generic
   - If location matters, include location context

2. COMPARISON (2 prompts): User comparing options in this space
   - Reference the actual category, not vague terms
   - Include audience context (e.g., "for small businesses", "in India")

3. SPECIFIC USE CASE (2 prompts): User has a specific need this entity serves
   - Match real tasks/problems the entity solves

4. EXPERT RECOMMENDATION (2 prompts): User wants trusted advice
   - Include specifics about the niche

5. LOCAL/REGIONAL (1 prompt): If location is relevant
   - Include city, country, or region in the prompt
   - If the entity is global, use a market-specific angle

6. ALTERNATIVE/EVALUATION (1 prompt): User evaluating options
   - "Is X worth it?", "What to look for in Y?"

CRITICAL RULES:
- NEVER mention "{brand_name}" in any prompt — these should be natural user queries
- Be SPECIFIC to what this entity actually does (not generic "best tools" prompts)
- If it's a local business → include location in at least 2 prompts
- If it's a product → ask about specific features, pricing, alternatives
- If it's a service → ask about hiring, evaluating, comparing providers
- If it's a person/personal brand → ask about expertise, credentials, content
- Write conversational, the way real people talk to AI assistants
- Each prompt MUST be something a potential customer/user would actually ask

Return ONLY a JSON array of {count} strings. No markdown, no explanations.
Example: ["What are the best...", "How do I...", "Which ... in Mumbai?"]"""

    try:
        raw = ask_single_llm(prompt, purpose="Generate Brand Prompts", max_tokens=1200)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        prompts = json.loads(raw)
        if isinstance(prompts, list) and len(prompts) > 0:
            return [str(p).strip() for p in prompts[:count] if str(p).strip()]
    except Exception as exc:
        logger.warning("generate_brand_prompts failed: %s", exc)

    # Fallback with whatever context we have
    niche = industry or "services"
    return [
        f"What are the best {niche} available today?",
        f"Compare the top {niche} platforms for businesses",
        f"Which {niche} do experts recommend in 2026?",
        f"How do I choose the right {niche} provider?",
        f"What should I look for in a {niche} platform?",
    ]


def fire_prompt_across_engines(
    prompt_text: str,
    brand_name: str,
    brand_url: str,
    runs: int = DEFAULT_RUNS,
) -> list[dict]:
    """
    Fire prompt_text to GPT/Claude/Gemini multiple times to handle randomness.

    With runs=3 and 3 engines → 9 total results.
    Returns list of dicts: engine, response_text, brand_mentioned, sentiment, confidence, rank_position
    """
    from .llm import ask_multiple_llms
    from .ai_visibility import _build_brand_aliases, _match_brand, _analyze_mention_quality, _check_ranking_position

    brand_aliases = _build_brand_aliases(brand_name, brand_url)
    all_results = []

    for run_idx in range(runs):
        try:
            responses = ask_multiple_llms(
                prompt_text,
                providers=["gpt", "claude", "gemini"],
                purpose=f"Prompt Track (run {run_idx + 1}/{runs})",
                max_tokens=512,
            )
        except Exception as exc:
            logger.warning("fire_prompt run %d failed: %s", run_idx + 1, exc)
            continue

        for provider_key, response_text in responses.items():
            engine = _ENGINE_MAP.get(provider_key, provider_key)

            if not response_text:
                all_results.append({
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

            all_results.append({
                "engine": engine,
                "response_text": response_text[:3000],
                "brand_mentioned": found,
                "sentiment": sentiment,
                "confidence": round(confidence, 3),
                "rank_position": rank_position,
            })

    # Also search Google via Serper (once per prompt, not per run)
    google_result = _search_google_serper(prompt_text, brand_name, brand_url)
    if google_result:
        all_results.append(google_result)

    return all_results


def compute_prompt_score(results: list[dict]) -> dict:
    """
    Compute weighted prompt ranking score from multiple results.

    Score = (Visibility × 0.5) + (Position Score × 0.3) + (Sentiment × 0.2)

    Returns dict with: score, visibility_pct, avg_position, sentiment, label
    """
    if not results:
        return {
            "score": 0.0,
            "visibility_pct": 0.0,
            "avg_position": 0,
            "sentiment": "neutral",
            "label": "Weak",
            "total_runs": 0,
            "mentions": 0,
        }

    total = len(results)
    mentions = sum(1 for r in results if r.get("brand_mentioned"))

    # Visibility: % of runs where brand appears (0-1)
    visibility = mentions / total if total else 0

    # Position Score: average 1/position (higher = better)
    positions = [r.get("rank_position", 0) for r in results if r.get("rank_position", 0) > 0]
    avg_position_score = (sum(1.0 / p for p in positions) / len(positions)) if positions else 0

    # Sentiment: +1 positive, 0 neutral, -1 negative → normalize to 0-1
    sentiment_map = {"positive": 1, "neutral": 0, "negative": -1}
    sentiments = [sentiment_map.get(r.get("sentiment", "neutral"), 0) for r in results]
    raw_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0
    norm_sentiment = (raw_sentiment + 1) / 2  # -1..1 → 0..1

    # Weighted score
    score = (visibility * 0.5) + (avg_position_score * 0.3) + (norm_sentiment * 0.2)
    score = min(score, 1.0)  # Cap at 1.0

    # Determine overall sentiment label
    if raw_sentiment > 0.3:
        sentiment_label = "positive"
    elif raw_sentiment < -0.3:
        sentiment_label = "negative"
    else:
        sentiment_label = "neutral"

    # Ranking label
    if score >= 0.6:
        label = "Strong"
    elif score >= 0.3:
        label = "Moderate"
    else:
        label = "Weak"

    return {
        "score": round(score, 3),
        "visibility_pct": round(visibility * 100, 1),
        "avg_position": round(1.0 / avg_position_score, 1) if avg_position_score > 0 else 0,
        "sentiment": sentiment_label,
        "label": label,
        "total_runs": total,
        "mentions": mentions,
    }


def recheck_track(track, brand_name: str, brand_url: str) -> int:
    """
    Re-fire a PromptTrack across all configured engines (3 runs × 3 engines),
    save PromptResult rows, compute and update the prompt score.

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

    # Compute and save score from ALL results (not just this run)
    all_results = list(
        track.results.values("brand_mentioned", "sentiment", "rank_position", "confidence")
    )
    score_data = compute_prompt_score(all_results)
    track.score = score_data["score"]
    track.save(update_fields=["score"])

    logger.info(
        "recheck_track #%d ('%s'): %d new results, score=%.3f (%s)",
        track.pk, track.prompt_text[:60], created, score_data["score"], score_data["label"],
    )
    return created
