"""
Prompt ranking engine — fires prompts across AI engines + search engines,
extracts signals, and computes 5-factor weighted AI visibility scores.

5-Factor Scoring (2026 AI Visibility Framework):
  Factor 1 — Authority & Credibility       (40% weight)
  Factor 2 — Content Quality & Utility     (35% weight)
  Factor 3 — Structural Extractability     (25% weight)
  Factor 4 — Semantic Alignment            (supplementary)
  Factor 5 — Third-Party Validation        (supplementary)

  Final Score = authority×0.40 + content_quality×0.35 + structural×0.25
"""
import json
import logging

logger = logging.getLogger("apps")

# Maps internal provider keys → PromptResult.Engine choices
_ENGINE_MAP = {
    "gpt": "chatgpt",
    "claude": "claude",
    "gemini": "gemini",
    "perplexity": "perplexity",
}

DEFAULT_RUNS = 3  # Fire each prompt N times to handle AI randomness

_TOKEN_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "from",
        "with",
        "inc",
        "llc",
        "ltd",
        "corp",
        "co",
        "year",
        "award",
        "awards",
        "best",
        "top",
        "site",
        "web",
        "www",
        "com",
        "org",
        "net",
        "http",
        "https",
    }
)


def _collect_brand_tokens(brand_name: str, brand_url: str = "") -> list[str]:
    """
    Build lowercase tokens to match inside prompt text: full name, meaningful
    words, and the registrable host label (e.g. lokmat from lokmat.com).
    """
    import re
    from urllib.parse import urlparse

    toks: list[str] = []
    bn = (brand_name or "").strip().lower()
    if bn:
        toks.append(bn)
        for part in re.split(r"[\s,|/&]+", bn):
            w = part.strip().strip("'\"")
            if len(w) >= 4 and w not in _TOKEN_STOP:
                toks.append(w)
            elif len(w) == 3 and w.isalpha() and w not in _TOKEN_STOP:
                toks.append(w)

    raw_url = (brand_url or "").strip()
    if raw_url:
        try:
            parsed = urlparse(raw_url if "://" in raw_url else f"https://{raw_url}")
            host = (parsed.netloc or "").lower().replace("www.", "")
            if host:
                label = host.split(":")[0]
                parts = label.split(".")
                junk_sub = frozenset(
                    {"www", "app", "m", "api", "blog", "cdn", "static", "shop", "store", "support", "help"}
                )
                if len(parts) >= 3:
                    sld = parts[-2]
                    if len(sld) >= 3 and sld not in {"localhost", "127"}:
                        toks.append(sld)
                    root = parts[0]
                    if (
                        root not in junk_sub
                        and len(root) >= 3
                        and root != sld
                        and root not in _TOKEN_STOP
                    ):
                        toks.append(root)
                elif len(parts) == 2:
                    root = parts[0]
                    if len(root) >= 3 and root not in {"localhost", "127"}:
                        toks.append(root)
        except Exception:
            pass

    seen: set[str] = set()
    out: list[str] = []
    for x in toks:
        x = (x or "").strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _prompt_mentions_brand(t: str, tokens: list[str]) -> bool:
    if not t or not tokens:
        return False
    for tok in tokens:
        if len(tok) < 2:
            continue
        if tok in t:
            return True
    return False


def classify_prompt_intent_and_type(
    prompt_text: str,
    brand_name: str = "",
    brand_url: str = "",
) -> tuple[str, str]:
    """
    Heuristic intent + query shape for GEO prompt rows.

    Returns (intent, prompt_type) as internal codes matching PromptTrack enums:
      intent: brand | informational | transactional
      prompt_type: organic | branded | competitive

    ``brand_url`` is used to derive host tokens (e.g. lokmat.com → lokmat) so
    prompts that never repeat the legal brand_name string still classify as
    branded when they mention the site / product name from the URL.
    """
    t = (prompt_text or "").lower()
    tokens = _collect_brand_tokens(brand_name, brand_url)

    transactional_kw = (
        "buy ",
        "buying",
        "purchase",
        "order ",
        "pricing",
        "price ",
        "prices",
        "cost ",
        "costs",
        "discount",
        "coupon",
        "deal",
        "trial",
        "demo",
        "sign up",
        "signup",
        "subscribe",
        "cheap",
        "affordable",
        "quote",
        "booking",
        "book a ",
        "book an ",
        "hire ",
        "free trial",
        "get a quote",
        "pay for",
        "checkout",
        "nominate",
        "nomination",
        "nominations",
        "register for",
        "registration",
        "apply for",
        "apply to ",
        "submit a ",
        "submit an ",
        "ticket",
        "tickets",
        "rsvp",
        "enroll",
        "enrollment",
        "vote for",
        "voting",
    )
    competitive_kw = (
        " vs ",
        " vs.",
        "versus",
        "compare ",
        "compared",
        "compare to",
        "compare with",
        "compared to",
        "compared with",
        "comparison",
        "alternative to",
        "alternatives to",
        "instead of",
        "better than",
        "head to head",
        "stack up",
        "competitor",
        "competition",
        "which is better",
        "who is better",
        "or better",
        "other regional",
        "against other",
        "among competitors",
    )

    has_brand = _prompt_mentions_brand(t, tokens)
    is_transactional = any(k in t for k in transactional_kw)
    is_competitive = any(k in t for k in competitive_kw) or (
        "how does" in t and "compare" in t
    )

    if is_competitive:
        query_type = "competitive"
    elif has_brand:
        query_type = "branded"
    else:
        query_type = "organic"

    if is_transactional:
        intent = "transactional"
    elif has_brand:
        intent = "brand"
    else:
        intent = "informational"

    return intent, query_type


def _providers_and_search_from_plan_engines(allowed_engines: list[str] | None) -> tuple[list[str], bool, bool]:
    """
    Map PLAN_LIMITS engine ids to OpenRouter provider keys and whether to run
    Google (Serper) and/or Bing searches.

    Returns: (provider_keys, include_google, include_bing)
    If allowed_engines is None, use full stack.
    """
    if allowed_engines is None:
        return (["gpt", "claude", "gemini", "perplexity"], True, True)
    s = {e.strip().lower() for e in allowed_engines if e}
    provs: list[str] = []
    if "chatgpt" in s:
        provs.append("gpt")
    if "gemini" in s:
        provs.append("gemini")
    if "claude" in s:
        provs.append("claude")
    if "perplexity" in s:
        provs.append("perplexity")
    include_google = "google" in s
    include_bing = "bing" in s
    if not provs and not include_google and not include_bing:
        provs = ["gemini"]
    return (provs, include_google, include_bing)


# Keep old name as alias so existing callers (tasks.py etc.) don't break
def _providers_and_google_from_plan_engines(allowed_engines: list[str] | None) -> tuple[list[str], bool]:
    provs, include_google, _ = _providers_and_search_from_plan_engines(allowed_engines)
    return (provs, include_google)


def _search_bing(query: str, brand_name: str, brand_url: str) -> dict:
    """
    Search Bing via the Azure Bing Web Search API and check if brand appears.
    Requires BING_API_KEY env var (Azure Cognitive Services key).

    Returns a PromptResult-compatible dict with engine="bing", or None on failure.
    """
    import os
    import requests as _requests
    from urllib.parse import urlparse

    api_key = os.getenv("BING_API_KEY", "")
    if not api_key:
        return None

    try:
        resp = _requests.get(
            "https://api.bing.microsoft.com/v7.0/search",
            headers={"Ocp-Apim-Subscription-Key": api_key},
            params={"q": query, "count": 10, "textDecorations": False},
            timeout=10,
        )
        if not resp.ok:
            logger.warning("Bing search failed: %d", resp.status_code)
            return None
        data = resp.json()
    except Exception as exc:
        logger.warning("Bing search error: %s", exc)
        return None

    brand_lower = brand_name.lower()
    domain = urlparse(brand_url).netloc.lower().replace("www.", "")
    web_pages = data.get("webPages", {}).get("value", [])
    answer_box = data.get("computation", {}) or data.get("answerBox", {})
    entities = data.get("entities", {}).get("value", [])

    mentioned = False
    rank_position = 0
    response_parts = []
    citations: list[dict] = []

    # Entity knowledge panel
    for entity in entities[:2]:
        name = (entity.get("name", "")).lower()
        desc = (entity.get("description", "")).lower()
        if brand_lower in name or brand_lower in desc or domain in name:
            mentioned = True
            rank_position = 1
            response_parts.append(f"[Entity] {entity.get('name', '')}: {entity.get('description', '')}")

    # Organic results
    for i, result in enumerate(web_pages[:10], 1):
        title = (result.get("name", "")).lower()
        snippet = (result.get("snippet", "")).lower()
        link = (result.get("url", "")).lower()
        result_domain = urlparse(link).netloc.lower().replace("www.", "")

        if brand_lower in title or brand_lower in snippet or domain == result_domain:
            mentioned = True
            if rank_position == 0:
                rank_position = i
            response_parts.append(f"[#{i}] {result.get('name', '')} — {result.get('snippet', '')}")
        else:
            response_parts.append(f"[#{i}] {result.get('name', '')}")

        if result.get("url"):
            citations.append({
                "url": result["url"],
                "title": result.get("name", "") or "",
                "snippet": result.get("snippet", "") or "",
                "position": i,
            })

    sentiment = "neutral"
    if mentioned:
        combined = " ".join(response_parts).lower()
        pos_words = ["best", "top", "leading", "recommend", "popular", "trusted", "great", "excellent"]
        neg_words = ["worst", "avoid", "poor", "bad", "scam", "issue", "problem"]
        pos_count = sum(1 for w in pos_words if w in combined)
        neg_count = sum(1 for w in neg_words if w in combined)
        if pos_count > neg_count:
            sentiment = "positive"
        elif neg_count > pos_count:
            sentiment = "negative"

    return {
        "engine": "bing",
        "response_text": "\n".join(response_parts[:5])[:3000],
        "brand_mentioned": mentioned,
        "sentiment": sentiment,
        "confidence": 1.0 if mentioned else 0.0,
        "rank_position": rank_position,
        "citations": citations,
    }


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
    citations: list[dict] = []

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

        if result.get("link"):
            citations.append({
                "url": result["link"],
                "title": result.get("title", "") or "",
                "snippet": result.get("snippet", "") or "",
                "position": i,
            })

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
        "citations": citations,
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
    allowed_engines: list[str] | None = None,
) -> list[dict]:
    """
    Fire prompt_text to all AI models (GPT/Claude/Gemini/Perplexity) multiple
    times plus Google and Bing search engines to handle randomness and gather
    multi-source ranking signals.

    With runs=3 and 4 LLMs + Google + Bing → up to 14 total results.
    allowed_engines: PLAN_LIMITS["engines"] list; None = all configured.
    Returns list of dicts: engine, response_text, brand_mentioned, sentiment, confidence, rank_position
    """
    from .llm import ask_multiple_llms_with_citations
    from .ai_visibility import _build_brand_aliases, _match_brand, _analyze_mention_quality, _check_ranking_position

    provider_keys, include_google, include_bing = _providers_and_search_from_plan_engines(allowed_engines)

    brand_aliases = _build_brand_aliases(brand_name, brand_url)
    all_results = []

    for run_idx in range(runs):
        responses: dict[str, dict] = {}
        if provider_keys:
            try:
                responses = ask_multiple_llms_with_citations(
                    prompt_text,
                    providers=provider_keys,
                    purpose=f"Prompt Track (run {run_idx + 1}/{runs})",
                    max_tokens=512,
                )
            except Exception as exc:
                logger.warning("fire_prompt run %d failed: %s", run_idx + 1, exc)
                continue

        for provider_key, payload in responses.items():
            engine = _ENGINE_MAP.get(provider_key, provider_key)
            response_text = (payload or {}).get("text", "") if isinstance(payload, dict) else ""
            provider_citations = (payload or {}).get("citations", []) if isinstance(payload, dict) else []

            if not response_text:
                all_results.append({
                    "engine": engine,
                    "response_text": "",
                    "brand_mentioned": False,
                    "sentiment": "neutral",
                    "confidence": 0.0,
                    "rank_position": 0,
                    "citations": provider_citations,
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
                "citations": provider_citations,
            })

    # Search engines run once per prompt (not per run) for authoritative signals
    if include_google:
        google_result = _search_google_serper(prompt_text, brand_name, brand_url)
        if google_result:
            all_results.append(google_result)

    if include_bing:
        bing_result = _search_bing(prompt_text, brand_name, brand_url)
        if bing_result:
            all_results.append(bing_result)

    return all_results


def compute_prompt_score(results: list[dict]) -> dict:
    """
    Compute 5-factor AI visibility ranking score from multi-engine results.

    2026 AI Visibility Ranking Framework:

    Factor 1 — Authority & Credibility (40%)
      Cross-engine coverage × 0.50 + mention rate × 0.30 + avg confidence × 0.20

    Factor 2 — Content Quality & Utility (35%)
      Positive sentiment rate × 0.60 + normalised sentiment × 0.40

    Factor 3 — Structural Extractability (25%)
      Top-3 position rate × 0.50 + avg inverse-position score × 0.50

    Factor 4 — Semantic Alignment (supplementary)
      mention_rate × avg_confidence_when_mentioned

    Factor 5 — Third-Party Validation (supplementary)
      Google/Bing mention weight (0.40) + AI cross-engine coverage (0.60)

    Final: score = authority×0.40 + content_quality×0.35 + structural×0.25
    """
    _SEARCH_ENGINES = {"google", "bing"}

    if not results:
        return {
            "score": 0.0,
            "visibility_pct": 0.0,
            "avg_position": 0,
            "sentiment": "neutral",
            "label": "Weak",
            "total_runs": 0,
            "mentions": 0,
            "authority_score": 0.0,
            "content_quality_score": 0.0,
            "structural_score": 0.0,
            "semantic_score": 0.0,
            "third_party_score": 0.0,
        }

    total = len(results)
    mentions = sum(1 for r in results if r.get("brand_mentioned"))
    mention_rate = mentions / total if total else 0.0

    # ── Shared signal helpers ──────────────────────────────────────────────
    sentiment_map = {"positive": 1, "neutral": 0, "negative": -1}
    raw_sentiments = [sentiment_map.get(r.get("sentiment", "neutral"), 0) for r in results]
    raw_sentiment_avg = sum(raw_sentiments) / len(raw_sentiments) if raw_sentiments else 0.0
    norm_sentiment = (raw_sentiment_avg + 1) / 2  # −1..1 → 0..1

    all_engines = {r.get("engine") for r in results if r.get("engine")}
    cited_engines = {r.get("engine") for r in results if r.get("brand_mentioned") and r.get("engine")}
    engine_coverage = len(cited_engines) / len(all_engines) if all_engines else 0.0

    avg_confidence = (
        sum(r.get("confidence", 0.0) for r in results) / total if total else 0.0
    )
    avg_confidence_when_mentioned = (
        sum(r.get("confidence", 0.0) for r in results if r.get("brand_mentioned")) / mentions
        if mentions else 0.0
    )

    # Position helpers
    positions = [r.get("rank_position", 0) for r in results if r.get("rank_position", 0) > 0]
    top3_count = sum(1 for p in positions if p <= 3)
    top3_rate = top3_count / total if total else 0.0
    avg_inv_pos = (sum(1.0 / p for p in positions) / len(positions)) if positions else 0.0

    # Positive/negative sentiment counts
    pos_count = sum(1 for r in results if r.get("sentiment") == "positive")
    pos_rate = pos_count / total if total else 0.0

    # ── Factor 1: Authority & Credibility (40%) ───────────────────────────
    authority_score = min(
        engine_coverage * 0.50 + mention_rate * 0.30 + avg_confidence * 0.20,
        1.0,
    )

    # ── Factor 2: Content Quality & Utility (35%) ─────────────────────────
    content_quality_score = min(
        pos_rate * 0.60 + norm_sentiment * 0.40,
        1.0,
    )

    # ── Factor 3: Structural Extractability (25%) ─────────────────────────
    structural_score = min(
        top3_rate * 0.50 + min(avg_inv_pos, 1.0) * 0.50,
        1.0,
    )

    # ── Factor 4: Semantic Alignment (supplementary) ──────────────────────
    semantic_score = min(mention_rate * avg_confidence_when_mentioned, 1.0)

    # ── Factor 5: Third-Party Validation (supplementary) ─────────────────
    search_results = [r for r in results if r.get("engine") in _SEARCH_ENGINES]
    search_mentioned = any(r.get("brand_mentioned") for r in search_results)
    search_signal = 1.0 if search_mentioned else 0.0

    ai_results = [r for r in results if r.get("engine") not in _SEARCH_ENGINES]
    ai_engines_all = {r.get("engine") for r in ai_results}
    ai_engines_cited = {r.get("engine") for r in ai_results if r.get("brand_mentioned")}
    ai_coverage = len(ai_engines_cited) / len(ai_engines_all) if ai_engines_all else 0.0

    third_party_score = min(search_signal * 0.40 + ai_coverage * 0.60, 1.0)

    # ── Composite score ───────────────────────────────────────────────────
    score = min(
        authority_score * 0.40
        + content_quality_score * 0.35
        + structural_score * 0.25,
        1.0,
    )

    # Sentiment label
    if raw_sentiment_avg > 0.3:
        sentiment_label = "positive"
    elif raw_sentiment_avg < -0.3:
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

    avg_display_position = round(1.0 / avg_inv_pos, 1) if avg_inv_pos > 0 else 0

    return {
        "score": round(score, 3),
        "visibility_pct": round(mention_rate * 100, 1),
        "avg_position": avg_display_position,
        "sentiment": sentiment_label,
        "label": label,
        "total_runs": total,
        "mentions": mentions,
        # 5-factor breakdown
        "authority_score": round(authority_score, 3),
        "content_quality_score": round(content_quality_score, 3),
        "structural_score": round(structural_score, 3),
        "semantic_score": round(semantic_score, 3),
        "third_party_score": round(third_party_score, 3),
    }


def recheck_track(track, brand_name: str, brand_url: str) -> int:
    """
    Re-fire a PromptTrack across all configured engines (3 runs × 3 engines),
    save PromptResult rows, compute and update the prompt score.

    Returns the number of new PromptResult rows created.
    """
    from django.db import close_old_connections
    from apps.accounts.subscription_utils import get_plan_limits, is_plan_limits_enforcement_enabled
    from .citations import persist_prompt_result, host_of, competitor_hosts_for_run

    close_old_connections()
    email = (track.analysis_run.email or "").strip()
    allowed = (
        get_plan_limits(email)["engines"]
        if is_plan_limits_enforcement_enabled() and email
        else None
    )
    engine_results = fire_prompt_across_engines(
        track.prompt_text, brand_name, brand_url, allowed_engines=allowed
    )
    brand_host = host_of(brand_url)
    rival_hosts = competitor_hosts_for_run(track.analysis_run)
    created = 0
    for r in engine_results:
        persist_prompt_result(track, r, brand_host, rival_hosts)
        created += 1

    # Compute and save score from ALL results (not just this run)
    all_results = list(
        track.results.values("brand_mentioned", "sentiment", "rank_position", "confidence", "engine")
    )
    score_data = compute_prompt_score(all_results)
    track.score = score_data["score"]
    track.authority_score = score_data["authority_score"]
    track.content_quality_score = score_data["content_quality_score"]
    track.structural_score = score_data["structural_score"]
    track.semantic_score = score_data["semantic_score"]
    track.third_party_score = score_data["third_party_score"]
    track.save(update_fields=[
        "score", "authority_score", "content_quality_score",
        "structural_score", "semantic_score", "third_party_score",
    ])

    logger.info(
        "recheck_track #%d ('%s'): %d new results, score=%.3f (%s)",
        track.pk, track.prompt_text[:60], created, score_data["score"], score_data["label"],
    )
    return created
