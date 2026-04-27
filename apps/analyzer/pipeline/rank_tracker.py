"""
Rank Tracker pipeline.

For a given AnalysisRun, auto-generate 10 tailored search queries (via
generate_brand_prompts) and, for each query, fetch top results from three
surfaces (Google organic, Reddit, Quora) via Serper.dev. Detect brand +
competitor mentions in every result.

Public API:
- build_queries_for_run(run) -> list[str]
- fetch_serper(query, num=10) -> list[dict]
- fetch_reddit(query) -> list[dict]
- fetch_quora(query) -> list[dict]
- detect_brand_mentions(title, snippet, brand_names, competitor_names) -> (bool, list[str])
- audit_query(query_row, brand_names, competitor_names) -> None
- run_rank_audit(audit_id) -> None  (orchestrator, called from daemon thread)
"""

from __future__ import annotations

import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

import requests
from django.db import close_old_connections

logger = logging.getLogger("apps")

SERPER_ENDPOINT = "https://google.serper.dev/search"
SERPER_TIMEOUT = 12
CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
DEFAULT_NUM_RESULTS = 10
PROMPT_COUNT = 10
AUDIT_CONCURRENCY = 8

# TLD → (country name, Google gl/hl code). Only entries with a real country.
_TLD_COUNTRY: dict[str, tuple[str, str]] = {
    "in": ("India", "in"), "uk": ("United Kingdom", "gb"),
    "au": ("Australia", "au"), "ca": ("Canada", "ca"),
    "de": ("Germany", "de"), "fr": ("France", "fr"),
    "jp": ("Japan", "jp"), "br": ("Brazil", "br"),
    "sg": ("Singapore", "sg"), "ae": ("UAE", "ae"),
    "za": ("South Africa", "za"), "ng": ("Nigeria", "ng"),
    "mx": ("Mexico", "mx"), "es": ("Spain", "es"),
    "it": ("Italy", "it"), "nl": ("Netherlands", "nl"),
    "ru": ("Russia", "ru"), "pl": ("Poland", "pl"),
    "ie": ("Ireland", "ie"), "nz": ("New Zealand", "nz"),
    "ph": ("Philippines", "ph"), "id": ("Indonesia", "id"),
    "my": ("Malaysia", "my"), "th": ("Thailand", "th"),
    "kr": ("South Korea", "kr"), "sa": ("Saudi Arabia", "sa"),
}
_COUNTRY_NAME_TO_CODE: dict[str, str] = {
    "india": "in", "united states": "us", "usa": "us", "us": "us",
    "united kingdom": "gb", "uk": "gb", "britain": "gb", "england": "gb",
    "australia": "au", "canada": "ca", "germany": "de", "france": "fr",
    "japan": "jp", "brazil": "br", "singapore": "sg", "uae": "ae",
    "south africa": "za", "nigeria": "ng", "mexico": "mx", "spain": "es",
    "italy": "it", "netherlands": "nl", "russia": "ru", "poland": "pl",
    "ireland": "ie", "new zealand": "nz", "philippines": "ph",
    "indonesia": "id", "malaysia": "my", "thailand": "th",
    "south korea": "kr", "saudi arabia": "sa",
}
# Region / city tokens that strongly imply a country (extend as needed).
_REGION_HINTS: list[tuple[str, str, str]] = [
    # (token, region label, country name)
    ("maharashtra", "Maharashtra, India", "India"),
    ("maharashtrian", "Maharashtra, India", "India"),
    ("mumbai", "Mumbai, India", "India"),
    ("pune", "Pune, India", "India"),
    ("delhi", "Delhi, India", "India"),
    ("bengaluru", "Bengaluru, India", "India"),
    ("bangalore", "Bengaluru, India", "India"),
    ("hyderabad", "Hyderabad, India", "India"),
    ("chennai", "Chennai, India", "India"),
    ("kolkata", "Kolkata, India", "India"),
    ("lokmat", "Maharashtra, India", "India"),
    ("dubai", "Dubai, UAE", "UAE"),
    ("london", "London, UK", "United Kingdom"),
    ("sydney", "Sydney, Australia", "Australia"),
    ("toronto", "Toronto, Canada", "Canada"),
    ("singapore", "Singapore", "Singapore"),
    ("johannesburg", "Johannesburg, South Africa", "South Africa"),
    ("cape town", "Cape Town, South Africa", "South Africa"),
    ("lagos", "Lagos, Nigeria", "Nigeria"),
    ("manila", "Manila, Philippines", "Philippines"),
    ("jakarta", "Jakarta, Indonesia", "Indonesia"),
]


def _derive_geo(run) -> dict:
    """Best-effort geo inference from AnalysisRun signals.

    Returns {country, gl, region, source} — any may be empty. Precedence:
    explicit run.country > region token in brand_name / url > TLD.
    """
    country_name = (run.country or "").strip()
    gl = _COUNTRY_NAME_TO_CODE.get(country_name.lower(), "")
    region = ""
    source = "country" if country_name else ""

    # Region/city tokens in brand_name or URL path often beat TLD signals
    haystack = f"{run.brand_name or ''} {run.url or ''}".lower()
    for tok, reg, ctry in _REGION_HINTS:
        if tok in haystack:
            region = reg
            if not country_name:
                country_name = ctry
                gl = _COUNTRY_NAME_TO_CODE.get(ctry.lower(), gl)
                source = source or "region_token"
            break

    if not country_name:
        try:
            host = urlparse(run.url or "").netloc.lower()
            tld = host.rsplit(".", 1)[-1] if "." in host else ""
            pair = _TLD_COUNTRY.get(tld)
            if pair:
                country_name, gl = pair[0], pair[1]
                source = source or "tld"
        except Exception:
            pass

    return {
        "country": country_name,
        "gl": gl,
        "region": region,
        "source": source,
    }


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------


def build_queries_for_run(run) -> list[str]:
    """Generate PROMPT_COUNT comparison-focused queries tailored to the brand.

    Prompts are framed as competitor-vs-brand / reasoning / evaluation questions
    that a real user would ask — so Google/Reddit/Quora/AI responses naturally
    surface competing brands side-by-side.
    """
    try:
        prompts = _generate_comparison_prompts(run, count=PROMPT_COUNT)
        if prompts:
            return prompts
    except Exception as exc:
        logger.warning("comparison prompt generator failed: %s", exc)

    # Fallback to the existing generic brand prompt generator
    try:
        from apps.analyzer.pipeline.prompt_tracker import generate_brand_prompts

        industry = ""
        try:
            first = run.competitors.exclude(industry="").values_list("industry", flat=True).first()
            if first:
                industry = first
        except Exception:
            pass

        return generate_brand_prompts(
            brand_name=run.brand_name or "",
            brand_url=run.url or "",
            industry=industry,
            country=run.country or "",
            count=PROMPT_COUNT,
        )
    except Exception as exc:
        logger.warning("build_queries_for_run: fallback generator failed: %s", exc)
        return []


def _generate_comparison_prompts(run, count: int = PROMPT_COUNT) -> list[str]:
    """Ask the LLM for `count` comparison/reasoning-style prompts."""
    import json as _json
    from apps.analyzer.pipeline.llm import ask_llm

    brand = (run.brand_name or "").strip() or urlparse(run.url or "").netloc
    brand_url = run.url or ""
    try:
        competitors = [
            c for c in run.competitors.values_list("name", flat=True) if c
        ][:8]
    except Exception:
        competitors = []

    industry = ""
    try:
        first = run.competitors.exclude(industry="").values_list("industry", flat=True).first()
        if first:
            industry = first
    except Exception:
        pass

    geo = _derive_geo(run)
    country = geo.get("country") or ""
    region = geo.get("region") or ""
    location_line = region or country or ""

    competitor_block = (
        "\n".join(f"- {c}" for c in competitors)
        if competitors
        else "(no tracked competitors — use well-known alternatives in this space)"
    )
    context = f"""Brand/Entity: {brand}
Website: {brand_url}
Industry: {industry or '(unknown)'}
Country: {country or '(unknown)'}
Region / city: {region or "(unspecified — use the brand's country scope)"}
Known competitors:
{competitor_block}"""

    location_rules = (
        f"""LOCATION IS CRITICAL — every prompt MUST target the brand's geography ({location_line}).
- Explicitly include the country or region in AT LEAST 4 out of {count} prompts (e.g., "…in {location_line}", "…for {country} businesses", "…{region} alternatives").
- Do NOT drift to other countries, even if some tracked competitors are from elsewhere — the brand's geography is the anchor.
- Prefer local city / state language where relevant (e.g., Maharashtra, Mumbai) over generic national framing if region is given.
- If the topic genuinely has no local nuance, you may still omit location for 1–2 prompts — but never invent a different country."""
        if location_line
        else "- If location is unspecified, use country-neutral framing. Do NOT invent a country."
    )

    prompt = f"""You are a GEO expert. Produce {count} realistic search prompts a buyer would use to discover and evaluate options in THE BRAND'S CATEGORY. The goal is to generate queries that surface multi-brand listicles, reviews, and comparisons — pages and AI answers where a smaller brand like the target can reasonably appear alongside bigger names.

CONTEXT:
{context}

{location_rules}

HARD RULES — READ CAREFULLY:
- NEVER mention "{brand}" in prompts (the user is discovering — they don't know this brand yet).
- AT MOST 1 prompt may name a single competitor by name, and ONLY in the form "alternatives to X" or "X vs [category]" (NOT "X vs Y" — no competitor-vs-competitor prompts).
- DO NOT write "<CompetitorA> vs <CompetitorB>" prompts — they only surface pages about those two specific brands and exclude smaller / newer players like this brand.
- Prompts should be category-first, use-case-first, problem-first, or price-first — NOT brand-first.
- Write conversational language a real human would type, not SEO keyword strings.
- Each prompt: 8 to 18 words.

Distribute across these intents (aim for this rough split):
1. Category + location listicles (3 prompts): "best <category> for <use case> in <location>", "top <category> brands for <audience>", "most trusted <category> in <location>"
   — These surface "Top 10" blog posts, Reddit threads, and AI answers that commonly include smaller brands.
2. Use-case / problem-driven (3 prompts): "<category> for <specific user problem>", "which <category> works for <audience or skin/hair/situation type>"
   — Narrow, specific needs where smaller niche brands tend to be recommended.
3. Decision reasoning (2 prompts): "how to choose the right <category>", "what should I look for when buying <category>", "what makes a good <category>"
4. Evaluation / sentiment (2 prompts): "pros and cons of <category>", "is <category> worth the money", "what do Reddit users say about <category>", "honest reviews of <category>"
   — Open-ended enough that multiple brand names appear.

AVOID:
- "X vs Y" head-to-head between two known competitors.
- Prompts that name more than one competitor.
- Vague one-word prompts like "best cosmetics" — always add a qualifier (location, audience, problem, or price).
- Prompts where the brand would obviously have zero chance of being mentioned.

Return ONLY a JSON array of {count} strings. No markdown, no explanations."""

    try:
        raw = ask_llm(prompt, purpose="Rank Tracker — Comparison Prompts", max_tokens=1200).strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = _json.loads(raw)
        if isinstance(parsed, list):
            out = [str(p).strip() for p in parsed if str(p).strip()][:count]
            if out:
                return out
    except Exception as exc:
        logger.warning("_generate_comparison_prompts parse failed: %s", exc)
    return []


# ---------------------------------------------------------------------------
# SERP fetch
# ---------------------------------------------------------------------------


def _serper_key() -> str:
    return os.getenv("SERPER_API_KEY", "")


def _cse_keys() -> tuple[str, str]:
    return os.getenv("GOOGLE_CSE_API_KEY", ""), os.getenv("GOOGLE_CSE_CX", "")


def _domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _subreddit_of(url: str) -> str:
    try:
        path = urlparse(url).path
        m = re.search(r"/r/([A-Za-z0-9_]+)/", path)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _post_serper(
    query: str,
    num: int = DEFAULT_NUM_RESULTS,
    gl: str = "",
) -> dict | None:
    key = _serper_key()
    if not key:
        return None
    body: dict = {"q": query, "num": num}
    if gl:
        body["gl"] = gl
        body["hl"] = "en"
    try:
        resp = requests.post(
            SERPER_ENDPOINT,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json=body,
            timeout=SERPER_TIMEOUT,
        )
        if not resp.ok:
            logger.warning("Serper non-OK for %r: %d", query, resp.status_code)
            return None
        return resp.json()
    except Exception as exc:
        logger.warning("Serper error for %r: %s", query, exc)
        return None


def _organic_to_rows(data: dict, expected_domain: str | None = None) -> list[dict]:
    rows: list[dict] = []
    for idx, item in enumerate(data.get("organic", [])[:DEFAULT_NUM_RESULTS], 1):
        url = item.get("link") or ""
        if not url:
            continue
        dom = _domain_of(url)
        if expected_domain and expected_domain not in dom:
            continue
        rows.append(
            {
                "position": item.get("position") or idx,
                "url": url,
                "domain": dom,
                "title": item.get("title") or "",
                "snippet": item.get("snippet") or "",
                "subreddit": _subreddit_of(url) if "reddit.com" in dom else "",
                "upvotes": None,
            }
        )
    return rows


def fetch_serper(
    query: str, num: int = DEFAULT_NUM_RESULTS, *, gl: str = ""
) -> list[dict]:
    """Google organic results via Serper.dev (localized by `gl`)."""
    data = _post_serper(query, num=num, gl=gl)
    if data is None:
        return _fetch_via_cse(query, num=num)
    return _organic_to_rows(data)


def fetch_reddit(query: str, *, gl: str = "") -> list[dict]:
    """Reddit results via Serper `site:reddit.com`."""
    data = _post_serper(f"{query} site:reddit.com", num=DEFAULT_NUM_RESULTS, gl=gl)
    if data is None:
        return []
    return _organic_to_rows(data, expected_domain="reddit.com")


def fetch_quora(query: str, *, gl: str = "") -> list[dict]:
    """Quora results via Serper `site:quora.com`."""
    data = _post_serper(f"{query} site:quora.com", num=DEFAULT_NUM_RESULTS, gl=gl)
    if data is None:
        return []
    return _organic_to_rows(data, expected_domain="quora.com")


# ---------------------------------------------------------------------------
# AI engine responses
# ---------------------------------------------------------------------------


AI_ENGINE_LABELS: dict[str, str] = {
    "gpt": "ChatGPT",
    "claude": "Claude",
    "gemini": "Gemini",
    "perplexity": "Perplexity",
}
AI_ENGINE_ORDER: list[str] = ["gpt", "claude", "gemini", "perplexity"]


def fetch_ai_responses(query: str) -> list[dict]:
    """Ask multiple AI engines the query and return one row per engine.

    Each row has surface='ai' and fields aligned with RankResult schema.
    Results are returned in AI_ENGINE_ORDER for stable positioning.
    """
    try:
        from apps.analyzer.pipeline.llm import ask_multiple_llms_with_citations
    except Exception as exc:
        logger.warning("ai fetch: llm module unavailable: %s", exc)
        return []

    try:
        responses = ask_multiple_llms_with_citations(
            query,
            providers=AI_ENGINE_ORDER,
            purpose="Rank Tracker — AI engine response",
            max_tokens=420,
        ) or {}
    except Exception as exc:
        logger.warning("ai fetch error for %r: %s", query, exc)
        return []

    rows: list[dict] = []
    for idx, engine in enumerate(AI_ENGINE_ORDER, 1):
        payload = responses.get(engine)
        if not payload:
            continue
        text = (payload.get("text") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "surface": "ai",
                "engine": engine,
                "position": idx,
                "url": "",
                "domain": "",
                "title": AI_ENGINE_LABELS.get(engine, engine),
                "snippet": text[:400],
                "response_text": text[:4000],
                "citations": payload.get("citations") or [],
                "upvotes": None,
                "subreddit": "",
            }
        )
    return rows


def _fetch_via_cse(query: str, num: int = DEFAULT_NUM_RESULTS) -> list[dict]:
    """Google CSE fallback when SERPER_API_KEY is unset (Google surface only)."""
    api_key, cx = _cse_keys()
    if not api_key or not cx:
        return []
    try:
        resp = requests.get(
            CSE_ENDPOINT,
            params={"key": api_key, "cx": cx, "q": query, "num": min(num, 10)},
            timeout=SERPER_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning("CSE non-OK for %r: %d", query, resp.status_code)
            return []
        data = resp.json()
    except Exception as exc:
        logger.warning("CSE error for %r: %s", query, exc)
        return []

    rows: list[dict] = []
    for idx, item in enumerate(data.get("items", []), 1):
        url = item.get("link") or ""
        if not url:
            continue
        dom = _domain_of(url)
        rows.append(
            {
                "position": idx,
                "url": url,
                "domain": dom,
                "title": item.get("title") or "",
                "snippet": item.get("snippet") or "",
                "subreddit": "",
                "upvotes": None,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Brand mention detection
# ---------------------------------------------------------------------------


def _compile_name_re(names: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for name in names:
        clean = (name or "").strip()
        if len(clean) < 2:
            continue
        try:
            pattern = re.compile(rf"\b{re.escape(clean)}\b", re.IGNORECASE)
        except re.error:
            continue
        compiled.append((clean, pattern))
    return compiled


_COLLAPSE_RE = re.compile(r"[^a-z0-9]+")

# Keyword-based sentiment scoring (cheap, deterministic, no LLM).
_POS_WORDS = {
    "best", "top", "leading", "recommend", "recommended", "popular", "trusted",
    "great", "excellent", "reliable", "favorite", "favourite", "love", "impressive",
    "premier", "respected", "prestigious", "renowned", "solid", "innovative", "award-winning",
    "winner", "winning", "prestige", "credible", "preferred", "strong",
}
_NEG_WORDS = {
    "worst", "avoid", "poor", "bad", "scam", "issue", "problem", "disappointed",
    "terrible", "horrible", "broken", "overrated", "overpriced", "flaws", "flawed",
    "complaints", "controversy", "fake", "shady", "unreliable", "dismal", "declining",
}


def compute_sentiment(text: str) -> str:
    """Cheap keyword-based sentiment: positive / neutral / negative."""
    if not text:
        return "neutral"
    lower = text.lower()
    pos = sum(1 for w in _POS_WORDS if w in lower)
    neg = sum(1 for w in _NEG_WORDS if w in lower)
    if pos > neg and pos >= 1:
        return "positive"
    if neg > pos and neg >= 1:
        return "negative"
    return "neutral"


def _collapse(s: str) -> str:
    return _COLLAPSE_RE.sub("", (s or "").lower())


def _name_variants(name: str) -> list[str]:
    """Useful variants of a brand/competitor name for matching.

    Includes the raw name, the collapsed (alphanumeric-only) form,
    and the base domain if the name looks like a URL.
    """
    out: list[str] = []
    raw = (name or "").strip()
    if not raw:
        return out
    out.append(raw)
    if "://" in raw or "." in raw:
        try:
            host = urlparse(raw if "://" in raw else f"https://{raw}").netloc
            host = host.lower().replace("www.", "")
            if host:
                out.append(host)
                base = host.split(".")[0]
                if base and len(base) >= 3:
                    out.append(base)
        except Exception:
            pass
    return out


def detect_brand_mentions(
    title: str,
    snippet: str,
    brand_names: list[str],
    competitor_names: list[str],
    *,
    result_domain: str = "",
    result_url: str = "",
    brand_domain: str = "",
) -> tuple[bool, list[str]]:
    """Detect brand / competitor mentions in a SERP result.

    Matching strategy (any one triggers a hit):
    1. Word-boundary regex against title + snippet (case-insensitive).
    2. Collapsed-form substring (strips spaces/punct) in title+snippet+domain+url.
       Catches "Lokmatmaharashtrian" vs "Lokmat Maharashtrian".
    3. Domain-identity: result's domain matches brand's own domain.
    """
    haystack = f"{title or ''} {snippet or ''}"
    haystack_collapsed = _collapse(f"{haystack} {result_domain} {result_url}")

    def _matches(label: str) -> bool:
        for variant in _name_variants(label):
            if len(variant) < 2:
                continue
            try:
                if re.search(rf"\b{re.escape(variant)}\b", haystack, re.IGNORECASE):
                    return True
            except re.error:
                pass
            collapsed_variant = _collapse(variant)
            # Only use collapsed-form matching for LONG names (≥ 8 chars). Short
            # collapsed names (e.g., "amazon") false-positive-match compounds
            # ("amazonbasics") because word boundaries are gone.
            if len(collapsed_variant) >= 8 and collapsed_variant in haystack_collapsed:
                return True
        return False

    brand_hit = False
    if brand_domain and result_domain:
        bd = brand_domain.lower().replace("www.", "")
        rd = result_domain.lower().replace("www.", "")
        if bd and (bd == rd or rd.endswith("." + bd)):
            brand_hit = True
    if not brand_hit:
        for name in brand_names:
            if _matches(name):
                brand_hit = True
                break

    comps_found: list[str] = []
    for name in competitor_names:
        if _matches(name):
            comps_found.append(name)
    return brand_hit, sorted(set(comps_found))


# ---------------------------------------------------------------------------
# Per-query orchestration
# ---------------------------------------------------------------------------


def audit_query(
    query_row,
    brand_names: list[str],
    competitor_names: list[str],
    *,
    brand_domain: str = "",
    gl: str = "",
) -> None:
    """Fetch all 3 surfaces for one query and persist RankResult rows.

    Caller must have opened fresh DB connections (run inside executor thread).
    """
    from apps.analyzer.models import RankResult, RankQuery

    try:
        fetchers = (
            ("google", lambda q: fetch_serper(q, gl=gl)),
            ("reddit", lambda q: fetch_reddit(q, gl=gl)),
            ("quora", lambda q: fetch_quora(q, gl=gl)),
            ("ai", fetch_ai_responses),
        )

        brand_hits = 0
        to_create: list[RankResult] = []
        for surface, fn in fetchers:
            try:
                rows = fn(query_row.prompt_text) or []
            except Exception as exc:
                logger.warning(
                    "audit_query surface=%s query=%r error: %s",
                    surface,
                    query_row.prompt_text[:80],
                    exc,
                )
                rows = []
            for row in rows:
                # For AI rows, search in the full response text, not just snippet
                search_snippet = row.get("response_text", "") or row.get("snippet", "")
                is_brand, comps = detect_brand_mentions(
                    row.get("title", ""),
                    search_snippet,
                    brand_names,
                    competitor_names,
                    result_domain=row.get("domain", ""),
                    result_url=row.get("url", ""),
                    brand_domain=brand_domain,
                )
                if is_brand:
                    brand_hits += 1
                sentiment = compute_sentiment(
                    f"{row.get('title') or ''} {search_snippet}"
                )
                to_create.append(
                    RankResult(
                        query=query_row,
                        surface=surface,
                        position=int(row.get("position") or 0),
                        url=(row.get("url") or "")[:2048],
                        domain=(row.get("domain") or "")[:255],
                        title=(row.get("title") or "")[:300],
                        snippet=(row.get("snippet") or "")[:4000],
                        engine=(row.get("engine") or "")[:64],
                        response_text=(row.get("response_text") or "")[:4000],
                        sentiment=sentiment,
                        is_brand_mentioned=is_brand,
                        competitors_mentioned=comps,
                        upvotes=row.get("upvotes"),
                        subreddit=(row.get("subreddit") or "")[:120],
                    )
                )

        if to_create:
            RankResult.objects.bulk_create(to_create)

        query_row.brand_mention_count = brand_hits
        query_row.status = RankQuery.Status.DONE
        query_row.save(update_fields=["brand_mention_count", "status"])
    except Exception as exc:
        logger.exception("audit_query fatal: %s", exc)
        try:
            query_row.status = RankQuery.Status.FAILED
            query_row.error_message = str(exc)[:500]
            query_row.save(update_fields=["status", "error_message"])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Audit orchestrator (daemon thread entry point)
# ---------------------------------------------------------------------------


def _top3_brand_hit(query_row) -> bool:
    """Did the brand appear in any top-3 SERP result, or in any AI response?"""
    if query_row.results.filter(surface="ai", is_brand_mentioned=True).exists():
        return True
    return query_row.results.filter(position__lte=3, is_brand_mentioned=True).exists()


def run_rank_audit(audit_id: int) -> None:
    """Orchestrator — runs in a daemon thread. Mutates RankAudit + rows."""
    from django.utils import timezone as djtz
    from apps.analyzer.models import RankAudit, RankQuery

    close_old_connections()

    try:
        audit = RankAudit.objects.select_related("analysis_run").get(pk=audit_id)
    except RankAudit.DoesNotExist:
        logger.warning("run_rank_audit: audit %s gone", audit_id)
        return

    audit.status = RankAudit.Status.RUNNING
    audit.progress = 1
    audit.started_at = djtz.now()
    audit.save(update_fields=["status", "progress", "started_at"])

    try:
        run = audit.analysis_run

        serper_ok = bool(_serper_key())
        cse_ok = all(_cse_keys())
        if not serper_ok and not cse_ok:
            audit.error_message = (
                "No SERP provider configured — set SERPER_API_KEY in backend .env "
                "(free 2,500/month at https://serper.dev) to fetch rankings."
            )
            audit.save(update_fields=["error_message"])

        brand_names = [n for n in (run.brand_name,) if n]
        try:
            brand_domain = urlparse(run.url or "").netloc.lower().replace("www.", "")
        except Exception:
            brand_domain = ""
        if brand_domain:
            brand_names.append(brand_domain)

        geo = _derive_geo(run)
        gl = geo.get("gl") or ""
        try:
            competitor_names = list(
                run.competitors.values_list("name", flat=True)
            )
        except Exception:
            competitor_names = []
        competitor_names = [c for c in competitor_names if c]

        queries = build_queries_for_run(run)
        queries = [q for q in queries if q and q.strip()][:PROMPT_COUNT]
        if not queries:
            audit.status = RankAudit.Status.FAILED
            audit.error_message = "Could not generate prompts for this brand"
            audit.finished_at = djtz.now()
            audit.progress = 100
            audit.save(
                update_fields=["status", "error_message", "finished_at", "progress"]
            )
            return

        query_rows: list[RankQuery] = []
        for idx, prompt in enumerate(queries, 1):
            query_rows.append(
                RankQuery.objects.create(
                    audit=audit, prompt_text=prompt.strip(), rank=idx
                )
            )

        audit.total_queries = len(query_rows)
        audit.progress = 5
        audit.save(update_fields=["total_queries", "progress"])

        done = 0
        lock = threading.Lock()

        def worker(q):
            close_old_connections()
            audit_query(q, brand_names, competitor_names, brand_domain=brand_domain, gl=gl)
            nonlocal done
            with lock:
                done += 1
                pct = 5 + int(90 * (done / max(1, len(query_rows))))
                try:
                    RankAudit.objects.filter(pk=audit.pk).update(
                        queries_done=done, progress=min(95, pct)
                    )
                except Exception:
                    pass

        with ThreadPoolExecutor(max_workers=AUDIT_CONCURRENCY) as ex:
            list(ex.map(worker, query_rows))

        total_mentions = 0
        top3_hits = 0
        for q in query_rows:
            q.refresh_from_db()
            total_mentions += int(q.brand_mention_count or 0)
            if _top3_brand_hit(q):
                top3_hits += 1

        n = max(1, len(query_rows))
        audit.avg_brand_mentions = round(total_mentions / n, 2)
        audit.avg_top3_brand_rate = round(top3_hits / n, 2)
        audit.status = RankAudit.Status.COMPLETE
        audit.progress = 100
        audit.queries_done = len(query_rows)
        audit.finished_at = djtz.now()
        audit.save(
            update_fields=[
                "avg_brand_mentions",
                "avg_top3_brand_rate",
                "status",
                "progress",
                "queries_done",
                "finished_at",
            ]
        )
    except Exception as exc:
        logger.exception("run_rank_audit fatal: %s", exc)
        try:
            audit.status = RankAudit.Status.FAILED
            audit.error_message = str(exc)[:1000]
            audit.finished_at = djtz.now()
            audit.progress = 100
            audit.save(
                update_fields=["status", "error_message", "finished_at", "progress"]
            )
        except Exception:
            pass
