import json
import logging
import re
from urllib.parse import urlparse

import requests

from .crawler import CrawlResult, crawl_page
from .content import score_content
from .schema import score_schema
from .eeat import score_eeat
from .technical import score_technical
from .aggregator import compute_static_composite
from .utils import extract_brand_name
from .market_profiler import build_brand_market_profile

logger = logging.getLogger("apps")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

VALID_TIERS = {"Tier 1", "Tier 2"}
VALID_REVENUE_BANDS = {"Bootstrap", "<10M", "10M-50M", "50M-200M", "200M+"}

# Minimum relevance score (out of 10) to include a competitor
RELEVANCE_THRESHOLD = 6
MARKET_CONFIDENCE_STRICT = 0.35
MARKET_CONFIDENCE_SOFT = 0.18

# TLD → country mapping (expand as needed)
TLD_COUNTRY_MAP = {
    ".in": "India",
    ".co.in": "India",
    ".uk": "United Kingdom",
    ".co.uk": "United Kingdom",
    ".de": "Germany",
    ".fr": "France",
    ".com.au": "Australia",
    ".com.br": "Brazil",
    ".ca": "Canada",
    ".sg": "Singapore",
    ".ae": "United Arab Emirates",
    ".pk": "Pakistan",
    ".ng": "Nigeria",
    ".za": "South Africa",
    ".mx": "Mexico",
    ".jp": "Japan",
    ".cn": "China",
    ".kr": "South Korea",
    ".id": "Indonesia",
    ".my": "Malaysia",
    ".ph": "Philippines",
    ".vn": "Vietnam",
    ".th": "Thailand",
    ".nz": "New Zealand",
    ".es": "Spain",
    ".it": "Italy",
    ".nl": "Netherlands",
    ".se": "Sweden",
    ".no": "Norway",
    ".dk": "Denmark",
    ".fi": "Finland",
    ".pl": "Poland",
    ".ru": "Russia",
    ".tr": "Turkey",
}

# Currency / phone prefix → country signals
CURRENCY_COUNTRY = {
    "₹": "India",
    "inr": "India",
    "rs.": "India",
    "r$": "Brazil",
    "brl": "Brazil",
    "£": "United Kingdom",
    "gbp": "United Kingdom",
    "a$": "Australia",
    "aud": "Australia",
    "c$": "Canada",
    "cad": "Canada",
    "s$": "Singapore",
    "sgd": "Singapore",
    "aed": "United Arab Emirates",
    "¥": "Japan",
    "jpy": "Japan",
    "₩": "South Korea",
    "krw": "South Korea",
    "zar": "South Africa",
    "mxn": "Mexico",
    "ngn": "Nigeria",
    "pkr": "Pakistan",
}

PHONE_PREFIX_COUNTRY = {
    "+91": "India",
    "+44": "United Kingdom",
    "+49": "Germany",
    "+33": "France",
    "+61": "Australia",
    "+55": "Brazil",
    "+1": None,  # ambiguous — US or Canada
    "+65": "Singapore",
    "+971": "United Arab Emirates",
    "+92": "Pakistan",
    "+234": "Nigeria",
    "+27": "South Africa",
    "+52": "Mexico",
    "+81": "Japan",
    "+82": "South Korea",
    "+62": "Indonesia",
    "+60": "Malaysia",
    "+63": "Philippines",
    "+84": "Vietnam",
    "+66": "Thailand",
    "+64": "New Zealand",
    "+34": "Spain",
    "+39": "Italy",
    "+31": "Netherlands",
    "+46": "Sweden",
    "+47": "Norway",
    "+45": "Denmark",
    "+358": "Finland",
    "+48": "Poland",
    "+7": "Russia",
    "+90": "Turkey",
}

COUNTRY_ALIAS_MAP = {
    "us": "United States",
    "usa": "United States",
    "u s": "United States",
    "u k": "United Kingdom",
    "uk": "United Kingdom",
    "uae": "United Arab Emirates",
}

KNOWN_COUNTRIES = sorted(
    {
        *TLD_COUNTRY_MAP.values(),
        *[c for c in CURRENCY_COUNTRY.values() if c],
        *[c for c in PHONE_PREFIX_COUNTRY.values() if c],
        "United States",
        "United Kingdom",
    },
    key=len,
    reverse=True,
)


GLOBAL_KEYWORDS = {
    "worldwide", "global", "internationally", "international",
    "across the globe", "all over the world", "50+ countries",
    "100+ countries", "180+ countries", "available in", "ships to",
    "serving customers in", "operating in", "offices in",
}

def _detect_country_from_signals(crawl: CrawlResult) -> tuple[str | None, bool]:
    """
    Detect the brand's country and whether it has global presence.

    Returns (country, is_global):
      - ("India", False)  → regional brand, apply country lock
      - (None, True)      → global brand, no country lock
      - (None, False)     → unknown, let LLM decide (soft preference only)

    Priority for country detection: TLD > currency > phone prefix > city/country keywords.
    Global detection: multiple distinct currencies/phone prefixes OR explicit global language.
    TLD is always definitive even for global brands (e.g. .in = Indian brand).
    """
    url = crawl.url or ""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    page_text = (crawl.text or "")[:8000]
    page_lower = page_text.lower()
    soup_text = (crawl.soup.get_text(separator=" ") if crawl.soup else "").lower()[:5000]

    # ── 1. Country-specific TLD — highest confidence, always a regional brand ──
    for tld, country in sorted(TLD_COUNTRY_MAP.items(), key=lambda x: -len(x[0])):
        if host.endswith(tld):
            logger.info("Country detected from TLD %s: %s", tld, country)
            return country, False  # regional even if they say "global"

    # ── 2. Global presence check (only relevant for .com / generic TLDs) ──
    # a) Explicit global language on the page
    global_language_count = sum(1 for kw in GLOBAL_KEYWORDS if kw in soup_text)

    # b) Multiple distinct currencies found → global pricing
    currencies_found = {country for symbol, country in CURRENCY_COUNTRY.items() if symbol in page_lower}
    multi_currency = len(currencies_found) >= 3

    # c) Multiple distinct country phone prefixes found
    phone_countries_found = set()
    for prefix, country in sorted(PHONE_PREFIX_COUNTRY.items(), key=lambda x: -len(x[0])):
        if country and prefix in page_text[:5000]:
            phone_countries_found.add(country)
    multi_phone = len(phone_countries_found) >= 3

    if multi_currency or multi_phone or global_language_count >= 2:
        logger.info(
            "Global brand detected: global_keywords=%d, currencies=%s, phone_countries=%s",
            global_language_count, currencies_found, phone_countries_found,
        )
        return None, True

    # ── 3. Single currency signal ──
    for symbol, country in CURRENCY_COUNTRY.items():
        if symbol in page_lower:
            logger.info("Country detected from currency '%s': %s", symbol, country)
            return country, False

    # ── 4. Single phone prefix ──
    for prefix, country in sorted(PHONE_PREFIX_COUNTRY.items(), key=lambda x: -len(x[0])):
        if country and prefix in page_text[:5000]:
            logger.info("Country detected from phone prefix %s: %s", prefix, country)
            return country, False

    # ── 5. City / country keywords in page text (last resort) ──
    country_keywords = {
        "india": "India", "bangalore": "India", "bengaluru": "India",
        "mumbai": "India", "delhi": "India", "hyderabad": "India",
        "chennai": "India", "pune": "India", "kolkata": "India",
        "ahmedabad": "India", "jaipur": "India", "noida": "India",
        "gurugram": "India", "gurgaon": "India",
        "united kingdom": "United Kingdom", "london": "United Kingdom",
        "germany": "Germany", "berlin": "Germany",
        "france": "France", "paris": "France",
        "australia": "Australia", "sydney": "Australia", "melbourne": "Australia",
        "brazil": "Brazil", "são paulo": "Brazil",
        "singapore": "Singapore",
        "dubai": "United Arab Emirates", "abu dhabi": "United Arab Emirates",
        "pakistan": "Pakistan", "karachi": "Pakistan", "lahore": "Pakistan",
        "nigeria": "Nigeria", "lagos": "Nigeria",
        "south africa": "South Africa", "johannesburg": "South Africa",
        "mexico": "Mexico", "mexico city": "Mexico",
        "indonesia": "Indonesia", "jakarta": "Indonesia",
        "malaysia": "Malaysia", "kuala lumpur": "Malaysia",
        "philippines": "Philippines", "manila": "Philippines",
        "vietnam": "Vietnam", "ho chi minh": "Vietnam",
        "thailand": "Thailand", "bangkok": "Thailand",
        "turkey": "Turkey", "istanbul": "Turkey",
        "poland": "Poland", "warsaw": "Poland",
        "netherlands": "Netherlands", "amsterdam": "Netherlands",
        "sweden": "Sweden", "stockholm": "Sweden",
        "spain": "Spain", "madrid": "Spain",
        "italy": "Italy", "rome": "Italy", "milan": "Italy",
        "japan": "Japan", "tokyo": "Japan",
        "south korea": "South Korea", "seoul": "South Korea",
        "china": "China", "beijing": "China", "shanghai": "China",
        "canada": "Canada", "toronto": "Canada", "vancouver": "Canada",
    }
    for keyword, country in country_keywords.items():
        if keyword in soup_text:
            logger.info("Country detected from page keyword '%s': %s", keyword, country)
            return country, False

    # ── 6. Unknown — no strong signal ──
    return None, False


def _build_deep_site_context(crawl: CrawlResult) -> str:
    soup = crawl.soup
    parts = []

    title = soup.find("title")
    if title and title.string:
        parts.append(f"Title: {title.string.strip()}")

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        parts.append(f"Description: {meta_desc['content'].strip()}")

    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        parts.append(f"OG Title: {og_title['content'].strip()}")

    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        parts.append(f"OG Desc: {og_desc['content'].strip()}")

    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw and meta_kw.get("content"):
        parts.append(f"Keywords: {meta_kw['content'].strip()[:200]}")

    headings = []
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = tag.get_text(strip=True)
        if text and len(text) > 3:
            headings.append(text)
    if headings:
        parts.append("Headings: " + " | ".join(headings[:20]))

    nav = soup.find("nav")
    if nav:
        nav_links = [a.get_text(strip=True) for a in nav.find_all("a") if a.get_text(strip=True)]
        if nav_links:
            parts.append("Nav: " + ", ".join(nav_links[:20]))

    body_text = crawl.text[:1200]
    if body_text:
        parts.append(f"Content: {body_text}")

    raw = "\n".join(parts)
    return re.sub(r"\s+", " ", raw).strip()[:2500]


def _market_profile_prompt_block(market_profile: dict | None) -> str:
    if not market_profile:
        return "No market profile available."
    top_market = market_profile.get("top_market") or "Unknown"
    model_type = market_profile.get("model_type") or "Unknown"
    confidence = market_profile.get("top_market_confidence", 0)
    country_scores = market_profile.get("country_scores", [])[:5]
    signals = market_profile.get("signals", {})
    return (
        f"Top market: {top_market}\n"
        f"Top market confidence: {confidence}\n"
        f"Model type: {model_type}\n"
        f"Country scores (top): {json.dumps(country_scores, ensure_ascii=True)}\n"
        f"Address signals: {json.dumps(signals.get('addresses', [])[:5], ensure_ascii=True)}\n"
        f"Address snippets: {json.dumps(signals.get('address_snippets', [])[:3], ensure_ascii=True)}\n"
        f"Phone prefixes: {json.dumps(signals.get('phone_prefixes', [])[:8], ensure_ascii=True)}\n"
        f"Currencies: {json.dumps(signals.get('currencies', [])[:8], ensure_ascii=True)}\n"
        f"Shipping: {json.dumps(signals.get('shipping', {}), ensure_ascii=True)}\n"
        f"Payments: {json.dumps(signals.get('payment_methods', [])[:8], ensure_ascii=True)}\n"
    )


def _understand_site(
    brand_name: str,
    site_context: str,
    detected_country: str | None = None,
    is_global: bool = False,
    market_profile: dict | None = None,
    user_country: str | None = None,
) -> dict:
    """
    Step 1: Deep-understand the site — extract location, industry, revenue, business model.
    If a country was already detected via hard signals, inject it so the LLM doesn't override it.
    """
    try:
        from .llm import ask_llm
        
        country_hint = (
            f"User-selected target country: {user_country}. "
            f"Treat this as authoritative and keep primary_country aligned to {user_country}."
            if (user_country or "").strip()
            else ""
        )

        prompt = (
            f"Analyze this website and describe exactly what it does.\n\n"
            f"Brand: {brand_name}\n"
            f"Site content:\n{site_context}\n"
            f"Market profiler signals:\n{_market_profile_prompt_block(market_profile)}\n"
            f"{country_hint}\n\n"
            f"Reply ONLY with a JSON object with these exact fields:\n"
            f'{{"product_category": "specific category (not generic like SaaS)", '
            f'"target_audience": "who buys this", '
            f'"business_model": "B2B / B2C / D2C / Marketplace / etc.", '
            f'"key_features": ["...", "..."], '
            f'"tech_stack": ["...", "..."], '
            f'"one_liner": "one sentence", '
            f'"industry": "specific industry label", '
            f'"primary_country": "country where business operates/is based", '
            f'"primary_city": "city if identifiable, else empty string", '
            f'"customer_segment": "SMB / Mid-Market / Enterprise / Consumer / DTC", '
            f'"estimated_annual_revenue_usd": "rough estimate like <1M, 1M-10M, 10M-50M, 50M-200M, 200M+"}}\n\n'
            f"Be specific — e.g. primary_country: India, customer_segment: SMB."
        )

        text = ask_llm(prompt, preferred_provider="gemini", max_tokens=600, purpose="Site Understanding")
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            if isinstance(data, dict) and data.get("product_category"):
                # Hard signals always override LLM's answer
                if user_country:
                    data["primary_country"] = user_country
                elif is_global:
                    data["primary_country"] = "Global"
                elif detected_country:
                    data["primary_country"] = detected_country
                return data
    except Exception as exc:
        logger.warning("Site understanding failed: %s", exc)

    # Fallback
    if user_country:
        return {"primary_country": user_country}
    if is_global:
        return {"primary_country": "Global"}
    return {"primary_country": detected_country} if detected_country else {}


def _normalize_country_name(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    cleaned = re.sub(r"[^\w\s]", " ", raw).lower()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None

    if cleaned in COUNTRY_ALIAS_MAP:
        return COUNTRY_ALIAS_MAP[cleaned]

    for country in KNOWN_COUNTRIES:
        pattern = rf"\b{re.escape(country.lower())}\b"
        if re.search(pattern, cleaned):
            return country

    return None


def _country_from_host(url: str) -> str | None:
    host = urlparse(url or "").netloc.lower()
    if not host:
        return None
    for tld, country in sorted(TLD_COUNTRY_MAP.items(), key=lambda x: -len(x[0])):
        if host.endswith(tld):
            return country
    return None


def _infer_competitor_country(comp: dict) -> str | None:
    geography_country = _normalize_country_name(comp.get("geography", ""))
    if geography_country:
        return geography_country
    return _country_from_host(comp.get("url", ""))


def _market_score_stats(market_profile: dict | None) -> tuple[float, float, float]:
    scores = (market_profile or {}).get("country_scores", []) or []
    top_score = 0.0
    second_score = 0.0
    if scores:
        try:
            top_score = float(scores[0].get("score") or 0.0)
        except (TypeError, ValueError, AttributeError):
            top_score = 0.0
    if len(scores) > 1:
        try:
            second_score = float(scores[1].get("score") or 0.0)
        except (TypeError, ValueError, AttributeError):
            second_score = 0.0
    dominance = top_score / max(second_score, 0.001) if top_score > 0 else 0.0
    return top_score, second_score, dominance


def _has_address_confirmation(top_market: str | None, market_profile: dict | None) -> bool:
    if not top_market:
        return False
    signals = (market_profile or {}).get("signals", {}) or {}

    normalized_addresses = {
        n for n in (_normalize_country_name(v) for v in signals.get("addresses", []) or []) if n
    }
    if top_market in normalized_addresses:
        return True

    pattern = rf"\b{re.escape(top_market.lower())}\b"
    for snippet in signals.get("address_snippets", []) or []:
        snippet_text = str(snippet or "").lower()
        if re.search(pattern, snippet_text):
            return True
    return False


def _market_gate_mode(
    primary_country: str | None,
    is_global: bool,
    market_profile: dict | None,
) -> tuple[str, str | None, float]:
    top_market = _normalize_country_name((market_profile or {}).get("top_market") or primary_country)
    try:
        confidence = float((market_profile or {}).get("top_market_confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    if is_global or not top_market:
        return "none", top_market, confidence
    top_score, _, dominance = _market_score_stats(market_profile)
    has_address = _has_address_confirmation(top_market, market_profile)

    if has_address:
        return "strict", top_market, confidence
    if confidence >= MARKET_CONFIDENCE_STRICT:
        return "strict", top_market, confidence
    if top_score >= 5.0 and dominance >= 1.25:
        return "strict", top_market, confidence
    if confidence >= MARKET_CONFIDENCE_SOFT:
        return "soft", top_market, confidence
    if top_score >= 2.5 and dominance >= 1.15:
        return "soft", top_market, confidence
    # If we have any plausible top market and brand is not global,
    # at least reject explicit cross-country mismatches.
    return "soft", top_market, confidence


def _apply_market_gate(
    competitors: list[dict],
    top_market: str | None,
    mode: str,
) -> tuple[list[dict], list[tuple[dict, str | None]]]:
    if mode == "none" or not top_market:
        return competitors, []

    accepted: list[dict] = []
    rejected: list[tuple[dict, str | None]] = []
    for comp in competitors:
        comp_country = _infer_competitor_country(comp)
        if mode == "strict":
            if comp_country == top_market:
                accepted.append(comp)
            else:
                rejected.append((comp, comp_country))
        else:
            # Soft mode: reject explicit mismatches, keep unknown-country candidates.
            if comp_country and comp_country != top_market:
                rejected.append((comp, comp_country))
            else:
                accepted.append(comp)
    return accepted, rejected


def _market_priority(comp: dict, top_market: str | None) -> int:
    if not top_market:
        return 0
    comp_country = _infer_competitor_country(comp)
    if comp_country == top_market:
        return 0
    if comp_country is None:
        return 1
    return 2


def _sort_competitors(candidates: list[dict], top_market: str | None = None) -> list[dict]:
    tier_order = {"Tier 1": 0, "Tier 2": 1}
    revenue_order = {"200M+": 0, "50M-200M": 1, "10M-50M": 2, "<10M": 3, "Bootstrap": 4, "Unknown": 5}
    return sorted(
        candidates,
        key=lambda c: (
            tier_order.get(c["tier"], 1),
            _market_priority(c, top_market),
            -(c["relevance_score"] or 0),
            revenue_order.get(c["estimated_revenue_band"], 5),
        ),
    )


def _discover_competitors_llm(
    brand_name: str,
    understanding: dict,
    site_context: str,
    detected_country: str | None = None,
    is_global: bool = False,
    is_marketplace: bool = False,
    market_profile: dict | None = None,
    user_country: str | None = None,
) -> list[dict]:
    """
    Step 2: Discover competitors with hard matching constraints on location, industry, revenue.
    """
    try:
        from .llm import ask_llm

        if understanding:
            product_category = understanding.get("product_category", "")
            target_audience = understanding.get("target_audience", "")
            industry = understanding.get("industry", "")
            # Market profiler country always wins, then hard-detected country, then LLM guess.
            profile_top_market = (market_profile or {}).get("top_market")
            primary_country = user_country or profile_top_market or detected_country or understanding.get("primary_country", "")
            primary_city = understanding.get("primary_city", "")
            business_model = understanding.get("business_model", "")
            customer_segment = understanding.get("customer_segment", "")
            brand_revenue = understanding.get("estimated_annual_revenue_usd", "")
            key_features = ", ".join(understanding.get("key_features", [])[:5])
            one_liner = understanding.get("one_liner", "")

            location_str = f"{primary_city}, {primary_country}".strip(", ") or primary_country or "unknown"

            understanding_block = (
                f"Product category: {product_category}\n"
                f"Industry: {industry}\n"
                f"Business model: {business_model}\n"
                f"Target audience: {target_audience}\n"
                f"Customer segment: {customer_segment}\n"
                f"Location: {location_str}\n"
                f"Estimated annual revenue: {brand_revenue}\n"
                f"Key features: {key_features}\n"
                f"One-liner: {one_liner}"
            )
            market_context_text = f"{product_category} {industry} {business_model}".lower()
        else:
            understanding_block = f"Site context:\n{site_context[:600]}"
            primary_country = user_country or ("Global" if is_global else "")
            customer_segment = ""
            brand_revenue = ""
            market_context_text = ""

        compact_context = _clean_site_context(site_context)
        allow_marketplaces = any(
            k in market_context_text
            for k in (
                "ecommerce",
                "online store",
                "retail",
                "beauty",
                "skincare",
                "cosmetic",
                "shop",
                "marketplace",
            )
        )
        exclusion_line = (
            "- Directories, media sites, or tangential tools with no buyer overlap "
            "(but include legitimate ecommerce retailers/marketplaces if buyers compare them)"
            if allow_marketplaces
            else "- Directories, media sites, aggregators, or tangential tools"
        )
        local_leader_preference = (
            "- For ecommerce/retail categories, prioritize country-relevant category leaders and major buyer-choice platforms before niche brands"
            if allow_marketplaces
            else ""
        )
        model_line = (
            "Brand model: Marketplace / multi-brand platform."
            if is_marketplace
            else "Brand model: Single-brand or standard ecommerce/service business."
        )
        marketplace_rules = (
            "- Since this is a marketplace, return competing marketplaces/platforms where buyers can compare multiple brands.\n"
            "- Exclude individual merchant brands, vendor storefronts, and sellers listed on the platform."
            if is_marketplace
            else ""
        )
        country_source_line = (
            f"User-selected target country from frontend: {user_country}. Treat this as a hard constraint."
            if user_country
            else f"Primary market country: {primary_country or 'Unknown'}."
        )
        prompt = (
            f"You are a senior competitive intelligence analyst.\n\n"
            f"Find exactly 5 DIRECT competitors for '{brand_name}'.\n\n"
            f"Brand profile:\n{understanding_block}\n\n"
            f"{model_line}\n"
            f"{country_source_line}\n"
            f"Market profiler:\n{_market_profile_prompt_block(market_profile)}\n"
            f"Additional site signals:\n{compact_context}\n\n"
            f"HARD REQUIREMENTS — every competitor MUST satisfy ALL of these:\n"
            f"1. Same industry and same specific product/service category\n"
            f"2. Same or closely substitutable business model for the same buyer intent "
            f"({understanding.get('business_model', 'same') or 'same'})\n"
            f"3. Same customer segment ({customer_segment or 'same as brand'})\n"
            f"4. {_geography_constraint(primary_country, is_global)}\n"
            f"5. Similar revenue scale — within 1-2 bands of the brand ({brand_revenue or 'unknown'})\n"
            f"6. Active company with a real, working homepage URL\n\n"
            f"{local_leader_preference}\n"
            f"{marketplace_rules}\n"
            f"STRICTLY EXCLUDE:\n"
            f"- The brand itself or its parent/subsidiaries\n"
            f"- {_geography_exclusion(primary_country, is_global)}\n"
            f"{exclusion_line}\n"
            f"- Enterprise giants when the brand is small/bootstrap (and vice versa)\n\n"
            f"Return ONLY a JSON array. Each object must have:\n"
            f"- name (string)\n"
            f"- url (homepage, must start with https://)\n"
            f"- industry (specific label)\n"
            f'- tier ("Tier 1" = direct clone, "Tier 2" = close alternative)\n'
            f"- target_market (SMB / Mid-Market / Enterprise / DTC / Consumer / Marketplace)\n"
            f"- geography (country/region the competitor primarily operates in)\n"
            f"- pricing_model (Subscription / Freemium / One-time / Custom / Free / etc.)\n"
            f"- estimated_revenue_band (Bootstrap / <10M / 10M-50M / 50M-200M / 200M+)\n"
            f"- positioning (1 sentence: their differentiation vs the brand)\n"
            f"- relevance_score (integer 1-10: how directly competitive they are with this brand)\n\n"
            f"Output ONLY the JSON array, no markdown, no explanation."
        )

        text = ask_llm(prompt, preferred_provider="gemini", max_tokens=1200, purpose="Competitor Discovery")
        candidates = _parse_competitors_from_llm(text, brand_name)

        gate_mode, gate_market, gate_conf = _market_gate_mode(
            primary_country=primary_country,
            is_global=is_global,
            market_profile=market_profile,
        )
        if user_country:
            gate_mode = "strict"
            gate_market = user_country
            gate_conf = 1.0
        top_score, second_score, dominance = _market_score_stats(market_profile)
        logger.info(
            "Market gate for %s: mode=%s market=%s conf=%.3f top_score=%.3f second=%.3f dominance=%.2f address_confirmed=%s",
            brand_name,
            gate_mode,
            gate_market,
            gate_conf,
            top_score,
            second_score,
            dominance,
            _has_address_confirmation(gate_market, market_profile),
        )
        gated, rejected = _apply_market_gate(candidates, gate_market, gate_mode)
        if rejected:
            logger.info(
                "Market gate (%s %.2f) removed %d/%d candidates for %s in %s",
                gate_mode,
                gate_conf,
                len(rejected),
                len(candidates),
                brand_name,
                gate_market,
            )

        selected = list(gated)
        if gate_mode in {"strict", "soft"} and gate_market and len(selected) < 5:
            needed = 5 - len(selected)
            existing_hosts = [urlparse(c["url"]).netloc.lower() for c in selected]
            refill_prompt = (
                f"{prompt}\n\n"
                f"REFILL MODE:\n"
                f"- Return exactly {needed} NEW competitors.\n"
                f"- Do not return any domain from this list: {json.dumps(existing_hosts, ensure_ascii=True)}\n"
                f"- ABSOLUTE MARKET LOCK: each competitor must primarily operate in {gate_market}.\n"
                f"- If you are not confident a company is in {gate_market}, do not include it.\n"
            )
            refill_text = ask_llm(
                refill_prompt,
                preferred_provider="gemini",
                max_tokens=900,
                purpose="Competitor Discovery Refill",
            )
            refill_candidates = _parse_competitors_from_llm(refill_text, brand_name)

            selected_hosts = {urlparse(c["url"]).netloc.lower() for c in selected}
            unique_refill = []
            for comp in refill_candidates:
                host = urlparse(comp["url"]).netloc.lower()
                if host in selected_hosts:
                    continue
                selected_hosts.add(host)
                unique_refill.append(comp)

            refill_gated, refill_rejected = _apply_market_gate(unique_refill, gate_market, gate_mode)
            if refill_rejected:
                logger.info(
                    "Refill market gate removed %d/%d candidates for %s in %s",
                    len(refill_rejected),
                    len(unique_refill),
                    brand_name,
                    gate_market,
                )
            selected.extend(refill_gated[:needed])

        sorted_candidates = _sort_competitors(
            selected,
            top_market=gate_market if gate_mode != "none" else None,
        )
        return sorted_candidates[:5]

    except Exception as exc:
        logger.warning("Competitor discovery failed: %s", exc)
    return []


def _geography_constraint(primary_country: str, is_global: bool) -> str:
    if is_global or primary_country in ("", "Global", "Unknown"):
        return "Global competitors are acceptable — find the best-matched competitors worldwide"
    return (
        f"COUNTRY LOCK — brand is based in {primary_country}. "
        f"Return competitors based in {primary_country} ONLY. "
        f"Do NOT return US/UK/other-country companies unless zero {primary_country} alternatives exist"
    )


def _geography_exclusion(primary_country: str, is_global: bool) -> str:
    if is_global or primary_country in ("", "Global", "Unknown"):
        return "Competitors from completely unrelated industries or regions with no overlap"
    return f"Companies NOT based in {primary_country} — hard exclusion unless no local alternative exists"


def _clean_site_context(text: str) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    compact = re.sub(r"[^\x20-\x7E]", " ", compact)
    compact = re.sub(r"\s+", " ", compact).strip()
    return compact[:800]


def _normalize_homepage_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://"):
        raw = "https://" + raw[len("http://"):]
    elif not raw.startswith("https://"):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    host = parsed.netloc.strip().lower()
    if not host:
        return ""
    return f"https://{host}"


def _clean_text(value: object, default: str = "Unknown", max_len: int = 120) -> str:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return text[:max_len]


def _normalize_tier(value: object) -> str:
    raw = str(value or "").strip()
    if raw in VALID_TIERS:
        return raw
    lower = raw.lower()
    if "tier 1" in lower or "direct" in lower:
        return "Tier 1"
    if "tier 2" in lower or "adjacent" in lower or "partial" in lower:
        return "Tier 2"
    return "Tier 1"


def _normalize_revenue_band(value: object) -> str:
    raw = str(value or "").strip()
    if raw in VALID_REVENUE_BANDS:
        return raw
    normalized = re.sub(r"\s+", "", raw).upper()
    mapping = {
        "BOOTSTRAP": "Bootstrap",
        "<10M": "<10M",
        "UNDER10M": "<10M",
        "10M-50M": "10M-50M",
        "50M-200M": "50M-200M",
        "200M+": "200M+",
        ">200M": "200M+",
        "200MPLUS": "200M+",
    }
    return mapping.get(normalized, "Unknown")


def _normalize_relevance_score(value: object) -> int | None:
    try:
        score = int(value)
        return max(1, min(10, score))
    except (TypeError, ValueError):
        return None


def _normalize_competitor_item(item: object, brand_name: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    name = _clean_text(item.get("name"), default="", max_len=255)
    if not name or name.lower() == (brand_name or "").strip().lower():
        return None

    url = _normalize_homepage_url(item.get("url", ""))
    if not url:
        return None

    return {
        "name": name,
        "url": url,
        "industry": _clean_text(item.get("industry"), default="", max_len=255),
        "tier": _normalize_tier(item.get("tier")),
        "target_market": _clean_text(item.get("target_market"), max_len=80),
        "geography": _clean_text(item.get("geography"), max_len=80),
        "pricing_model": _clean_text(item.get("pricing_model"), max_len=80),
        "estimated_revenue_band": _normalize_revenue_band(item.get("estimated_revenue_band")),
        "positioning": _clean_text(item.get("positioning"), max_len=240),
        "relevance_score": _normalize_relevance_score(item.get("relevance_score")),
    }


def _parse_competitors_from_llm(text: str, brand_name: str) -> list[dict]:
    match = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not match:
        return []

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    normalized: list[dict] = []
    seen_hosts: set[str] = set()
    for item in data:
        comp = _normalize_competitor_item(item, brand_name)
        if not comp:
            continue
        if comp["relevance_score"] is not None and comp["relevance_score"] < RELEVANCE_THRESHOLD:
            logger.info(
                "Dropping competitor %s: relevance score %d < threshold %d",
                comp["name"], comp["relevance_score"], RELEVANCE_THRESHOLD,
            )
            continue
        host = urlparse(comp["url"]).netloc.lower()
        if host in seen_hosts:
            continue
        seen_hosts.add(host)
        normalized.append(comp)
    return normalized


def _validate_url(url: str) -> bool:
    try:
        resp = requests.head(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=3,
            allow_redirects=True,
        )
        return resp.status_code < 400
    except requests.RequestException:
        return False


def discover_competitors(crawl: CrawlResult, user_country: str | None = None) -> list[dict]:
    if not crawl.ok:
        return []

    soup = crawl.soup
    brand_name = extract_brand_name(soup, crawl.url)

    site_context = _build_deep_site_context(crawl)

    # Build multi-page market profile BEFORE asking the LLM.
    market_profile = build_brand_market_profile(crawl, max_pages=8)
    model_details = market_profile.get("model_details") or {}
    is_marketplace = (
        market_profile.get("model_type") == "marketplace"
        or int(model_details.get("product_brand_count") or 0) >= 8
    )
    normalized_user_country = _normalize_country_name(user_country) or (str(user_country or "").strip() or None)
    detected_country = normalized_user_country or market_profile.get("top_market")
    is_global = False if normalized_user_country else (market_profile.get("model_type") == "global_dropshipping")
    logger.info(
        "Market profiler for %s: top_market=%s conf=%s model=%s marketplace=%s top_scores=%s addresses=%s",
        brand_name,
        market_profile.get("top_market"),
        market_profile.get("top_market_confidence"),
        market_profile.get("model_type"),
        is_marketplace,
        market_profile.get("country_scores", [])[:3],
        (market_profile.get("signals", {}) or {}).get("addresses", [])[:3],
    )

    # Fallback to legacy single-page detector if market profile has weak country signals.
    if not detected_country:
        legacy_country, legacy_global = _detect_country_from_signals(crawl)
        detected_country = legacy_country
        is_global = is_global or legacy_global
    if is_global:
        logger.info("Global brand detected for %s — no country lock", brand_name)
    elif detected_country:
        logger.info("Hard country signal for %s: %s", brand_name, detected_country)

    understanding = _understand_site(
        brand_name,
        site_context,
        detected_country,
        is_global,
        market_profile=market_profile,
        user_country=normalized_user_country,
    )
    if understanding:
        logger.info(
            "Site understanding for %s: category=%s, location=%s/%s, revenue=%s, global=%s",
            brand_name,
            understanding.get("product_category", ""),
            understanding.get("primary_city", ""),
            understanding.get("primary_country", ""),
            understanding.get("estimated_annual_revenue_usd", ""),
            is_global,
        )

    competitors = _discover_competitors_llm(
        brand_name,
        understanding,
        site_context,
        detected_country,
        is_global,
        is_marketplace=is_marketplace,
        market_profile=market_profile,
        user_country=normalized_user_country,
    )

    validated = []
    for comp in competitors:
        if _validate_url(comp["url"]):
            validated.append(comp)
        elif comp["url"].startswith("http://"):
            https_url = comp["url"].replace("http://", "https://", 1)
            if _validate_url(https_url):
                comp["url"] = https_url
                validated.append(comp)

    logger.info(
        "Competitors for %s: %d discovered, %d validated",
        brand_name, len(competitors), len(validated),
    )
    return validated[:5]


def score_competitor(url: str) -> tuple[dict | None, float]:
    """Crawl and score a competitor using static-only pillars."""
    crawl = crawl_page(url)
    if not crawl.ok:
        return None, 0.0

    content_score, content_details = score_content(crawl)
    schema_score_val, schema_details = score_schema(crawl)
    eeat_score_val, eeat_details = score_eeat(crawl)
    technical_score_val, technical_details = score_technical(crawl)

    composite = compute_static_composite(
        content_score, schema_score_val, eeat_score_val, technical_score_val
    )

    page_data = {
        "url": url,
        "content_score": content_score,
        "content_details": content_details,
        "schema_score": schema_score_val,
        "schema_details": schema_details,
        "eeat_score": eeat_score_val,
        "eeat_details": eeat_details,
        "technical_score": technical_score_val,
        "technical_details": technical_details,
        "composite_score": composite,
    }

    return page_data, composite
