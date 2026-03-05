import json
import logging
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .crawler import CrawlResult, crawl_page

logger = logging.getLogger("apps")

# Global currency code/symbol hints.
CURRENCY_MARKET_HINTS = {
    "USD": "United States",
    "$": "United States",
    "EUR": "European Union",
    "GBP": "United Kingdom",
    "INR": "India",
    "\u20b9": "India",
    "AUD": "Australia",
    "CAD": "Canada",
    "SGD": "Singapore",
    "AED": "United Arab Emirates",
    "JPY": "Japan",
    "CNY": "China",
    "KRW": "South Korea",
    "BRL": "Brazil",
    "MXN": "Mexico",
    "ZAR": "South Africa",
    "TRY": "Turkey",
    "SAR": "Saudi Arabia",
}

# International dialing-code market hints.
PHONE_MARKET_HINTS = {
    # Ambiguous; avoid forcing US/Canada unless other signals support it.
    "+1": None,
    "+7": "Russia/Kazakhstan",
    "+20": "Egypt",
    "+27": "South Africa",
    "+31": "Netherlands",
    "+32": "Belgium",
    "+33": "France",
    "+34": "Spain",
    "+39": "Italy",
    "+40": "Romania",
    "+41": "Switzerland",
    "+43": "Austria",
    "+44": "United Kingdom",
    "+45": "Denmark",
    "+46": "Sweden",
    "+47": "Norway",
    "+48": "Poland",
    "+49": "Germany",
    "+52": "Mexico",
    "+54": "Argentina",
    "+55": "Brazil",
    "+56": "Chile",
    "+57": "Colombia",
    "+60": "Malaysia",
    "+61": "Australia",
    "+62": "Indonesia",
    "+63": "Philippines",
    "+64": "New Zealand",
    "+65": "Singapore",
    "+66": "Thailand",
    "+81": "Japan",
    "+82": "South Korea",
    "+84": "Vietnam",
    "+86": "China",
    "+90": "Turkey",
    "+91": "India",
    "+92": "Pakistan",
    "+93": "Afghanistan",
    "+94": "Sri Lanka",
    "+95": "Myanmar",
    "+98": "Iran",
    "+212": "Morocco",
    "+216": "Tunisia",
    "+218": "Libya",
    "+234": "Nigeria",
    "+254": "Kenya",
    "+255": "Tanzania",
    "+256": "Uganda",
    "+263": "Zimbabwe",
    "+351": "Portugal",
    "+352": "Luxembourg",
    "+353": "Ireland",
    "+354": "Iceland",
    "+358": "Finland",
    "+380": "Ukraine",
    "+420": "Czech Republic",
    "+421": "Slovakia",
    "+971": "United Arab Emirates",
    "+972": "Israel",
    "+973": "Bahrain",
    "+974": "Qatar",
    "+975": "Bhutan",
    "+976": "Mongolia",
    "+977": "Nepal",
    "+966": "Saudi Arabia",
}

COUNTRY_CODE_HINTS = {
    "IN": "India",
    "IND": "India",
    "US": "United States",
    "USA": "United States",
    "GB": "United Kingdom",
    "UK": "United Kingdom",
    "DE": "Germany",
    "FR": "France",
    "AU": "Australia",
    "CA": "Canada",
    "SG": "Singapore",
    "AE": "United Arab Emirates",
    "JP": "Japan",
    "CN": "China",
    "KR": "South Korea",
    "BR": "Brazil",
    "MX": "Mexico",
    "ZA": "South Africa",
    "TR": "Turkey",
    "SA": "Saudi Arabia",
    "PK": "Pakistan",
    "ID": "Indonesia",
    "MY": "Malaysia",
    "PH": "Philippines",
    "TH": "Thailand",
    "VN": "Vietnam",
    "NZ": "New Zealand",
    "ES": "Spain",
    "IT": "Italy",
    "NL": "Netherlands",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "PL": "Poland",
    "IE": "Ireland",
    "PT": "Portugal",
}

PAGE_HINTS = (
    "product",
    "products",
    "shop",
    "store",
    "collection",
    "collections",
    "category",
    "categories",
    "shipping",
    "returns",
    "refund",
    "about",
    "contact",
    "faq",
)

PAYMENT_METHOD_HINTS = (
    "upi",
    "razorpay",
    "stripe",
    "paypal",
    "paytm",
    "phonepe",
    "gpay",
    "google pay",
    "apple pay",
    "cash on delivery",
    "cod",
)

MARKETPLACE_STRONG_HINTS = (
    "sell on",
    "become a seller",
    "become a vendor",
    "seller dashboard",
    "vendor dashboard",
    "merchant dashboard",
    "marketplace",
    "independent sellers",
    "third-party sellers",
)

MARKETPLACE_SOFT_HINTS = (
    "shop by brand",
    "all brands",
    "multiple brands",
    "multi brand",
    "our sellers",
    "seller portal",
    "vendor portal",
)

SINGLE_BRAND_HINTS = (
    "official store",
    "our brand story",
    "about our brand",
    "we design and manufacture",
)

GLOBAL_SHIPPING_HINTS = (
    "ships worldwide",
    "worldwide shipping",
    "international shipping",
    "global shipping",
    "ship globally",
)

COUNTRY_NAME_HINTS = {
    "india": "India",
    "united states": "United States",
    "usa": "United States",
    "uk": "United Kingdom",
    "united kingdom": "United Kingdom",
    "germany": "Germany",
    "france": "France",
    "australia": "Australia",
    "canada": "Canada",
    "singapore": "Singapore",
    "united arab emirates": "United Arab Emirates",
    "uae": "United Arab Emirates",
    "japan": "Japan",
    "china": "China",
    "south korea": "South Korea",
    "brazil": "Brazil",
    "mexico": "Mexico",
    "south africa": "South Africa",
    "turkey": "Turkey",
    "saudi arabia": "Saudi Arabia",
    "pakistan": "Pakistan",
    "indonesia": "Indonesia",
    "malaysia": "Malaysia",
    "philippines": "Philippines",
    "thailand": "Thailand",
    "vietnam": "Vietnam",
    "new zealand": "New Zealand",
    "spain": "Spain",
    "italy": "Italy",
    "netherlands": "Netherlands",
    "sweden": "Sweden",
    "norway": "Norway",
    "denmark": "Denmark",
    "finland": "Finland",
    "poland": "Poland",
    "ireland": "Ireland",
    "portugal": "Portugal",
}


def _slug(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.path or "/").lower()


def _pick_market_pages(seed: CrawlResult, max_pages: int = 8) -> list[str]:
    chosen = [seed.url]
    seen = {seed.url.rstrip("/")}

    scored_links = []
    for link in seed.internal_links:
        lower = _slug(link)
        score = 0
        for hint in PAGE_HINTS:
            if hint in lower:
                score += 1
        if score > 0:
            scored_links.append((score, link))

    scored_links.sort(key=lambda x: x[0], reverse=True)
    for _, link in scored_links:
        clean = link.rstrip("/")
        if clean in seen:
            continue
        chosen.append(link)
        seen.add(clean)
        if len(chosen) >= max_pages:
            break

    return chosen


def _extract_json_ld_objects(html: str) -> list[object]:
    objects = []
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
            objects.append(parsed)
        except json.JSONDecodeError:
            # Ignore malformed JSON-LD blocks.
            continue
    return objects


def _walk_objects(node: object):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_objects(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_objects(item)


def _extract_address_countries_from_jsonld(html: str) -> list[str]:
    markets: list[str] = []
    for obj in _extract_json_ld_objects(html):
        for node in _walk_objects(obj):
            if not isinstance(node, dict):
                continue
            if "addressCountry" in node and node.get("addressCountry"):
                markets.append(str(node["addressCountry"]).strip())
    return markets


def _extract_product_brands_from_jsonld(html: str) -> list[str]:
    brands: list[str] = []
    for obj in _extract_json_ld_objects(html):
        for node in _walk_objects(obj):
            if not isinstance(node, dict):
                continue
            node_type = str(node.get("@type", "")).lower()
            if "product" not in node_type:
                continue
            brand = node.get("brand")
            if isinstance(brand, dict):
                name = str(brand.get("name", "")).strip()
            else:
                name = str(brand or "").strip()
            if len(name) >= 3:
                brands.append(name)
    return brands


def _extract_address_snippets(text: str) -> list[str]:
    snippets: list[str] = []
    if not text:
        return snippets

    # Explicit address labels.
    for m in re.finditer(
        r"(?:address|registered office|head office|office address)\s*[:\-]\s*([^\n]{25,260})",
        text,
        flags=re.IGNORECASE,
    ):
        snippet = re.sub(r"\s+", " ", m.group(1)).strip(" ,.;")
        if len(snippet) >= 20:
            snippets.append(snippet[:260])

    # Postal-like lines with separators and mixed alnum tokens.
    for m in re.finditer(
        r"([A-Za-z0-9#\-/,\s]{20,220}(?:\b\d{4,6}\b)[A-Za-z0-9#\-/,\s]{0,80})",
        text,
        flags=re.IGNORECASE,
    ):
        snippet = re.sub(r"\s+", " ", m.group(1)).strip(" ,.;")
        if len(snippet) >= 25 and "," in snippet:
            snippets.append(snippet[:260])

    # Deduplicate while preserving order.
    seen = set()
    out = []
    for s in snippets:
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out[:8]


def _infer_market_from_address_snippet(snippet: str) -> str | None:
    s = (snippet or "").lower()
    if not s:
        return None
    for token, market in COUNTRY_NAME_HINTS.items():
        if re.search(rf"\b{re.escape(token)}\b", s):
            return market
    return None


def _normalize_market_label(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None

    upper = raw.upper()
    if upper in COUNTRY_CODE_HINTS:
        return COUNTRY_CODE_HINTS[upper]

    lower = raw.lower()
    if lower in COUNTRY_NAME_HINTS:
        return COUNTRY_NAME_HINTS[lower]

    # Keep multi-word names stable; title-case short free text labels.
    if len(raw) <= 3 and raw.isalpha():
        return None
    return " ".join(part.capitalize() for part in raw.split())


def _extract_phone_prefixes(text: str) -> list[str]:
    matches = re.findall(r"\+\d{1,3}", text or "")
    return list(dict.fromkeys(matches))


def _extract_currency_hints(text: str) -> list[str]:
    upper = (text or "").upper()
    found = []
    for token in CURRENCY_MARKET_HINTS:
        if token in ("$", "\u20b9"):
            if token in (text or ""):
                found.append(token)
        elif re.search(rf"\b{re.escape(token)}\b", upper):
            found.append(token)
    return list(dict.fromkeys(found))


def _extract_shipping_hints(text: str) -> dict:
    t = (text or "").lower()
    shipping_policy = bool(
        ("shipping" in t and "policy" in t)
        or ("delivery" in t and "policy" in t)
        or ("ships to" in t)
    )
    is_global = any(h in t for h in GLOBAL_SHIPPING_HINTS)
    return {"shipping_policy_found": shipping_policy, "ships_worldwide": is_global}


def _extract_payment_hints(text: str) -> list[str]:
    t = (text or "").lower()
    return [h for h in PAYMENT_METHOD_HINTS if h in t]


def _extract_domain_hint(url: str) -> str | None:
    host = (urlparse(url).netloc or "").lower()
    if not host:
        return None
    parts = host.split(".")
    if len(parts) < 2:
        return None
    tld = parts[-1]
    if len(tld) == 2:
        return tld.upper()
    if len(parts) >= 3 and len(parts[-2]) == 2:
        return parts[-2].upper()
    return None


def _extract_language_hint(html_lang: str) -> str | None:
    if not html_lang:
        return None
    m = re.match(r"^([a-z]{2})(?:[-_][A-Za-z]{2})?$", html_lang.strip(), flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1).lower()


def _extract_timezone_hints(text: str) -> list[str]:
    matches = re.findall(r"\bUTC[+-]\d{1,2}(?::\d{2})?\b", text or "", flags=re.IGNORECASE)
    return list(dict.fromkeys(matches))


def _build_signal_text(crawl: CrawlResult) -> str:
    """
    Build text for market signals without dropping footer/header/nav,
    because address/contact details often live there.
    """
    if not crawl.html:
        return crawl.text or ""
    soup = BeautifulSoup(crawl.html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _to_probabilities(score_map: dict[str, float]) -> list[dict]:
    if not score_map:
        return []
    positive = {k: v for k, v in score_map.items() if v > 0}
    total = sum(positive.values())
    if total <= 0:
        return []
    ranked = sorted(positive.items(), key=lambda x: x[1], reverse=True)
    return [
        {
            "market": market,
            "score": round(score, 3),
            "probability": round(score / total, 4),
        }
        for market, score in ranked
    ]


def _classify_business_model(aggregate_text: str, ships_worldwide: bool, market_count: int) -> str:
    t = (aggregate_text or "").lower()
    dropship_keywords = ("dropship", "dropshipping", "fulfilled by supplier", "supplier ships")
    has_dropship = any(k in t for k in dropship_keywords)

    if has_dropship and (ships_worldwide or market_count >= 3):
        return "global_dropshipping"
    if has_dropship:
        return "regional_dropshipping"
    return "local_d2c"


def _classify_business_model_v2(
    aggregate_text: str,
    ships_worldwide: bool,
    market_count: int,
    product_brand_count: int,
) -> tuple[str, dict]:
    t = (aggregate_text or "").lower()

    strong_hits = sum(1 for h in MARKETPLACE_STRONG_HINTS if h in t)
    soft_hits = sum(1 for h in MARKETPLACE_SOFT_HINTS if h in t)
    single_brand_hits = sum(1 for h in SINGLE_BRAND_HINTS if h in t)

    marketplace_score = (
        (3.0 if strong_hits > 0 else 0.0)
        + min(2.0, soft_hits * 0.5)
        + (2.0 if product_brand_count >= 6 else 0.0)
        - min(2.0, single_brand_hits * 0.5)
    )

    if marketplace_score >= 3.0:
        return "marketplace", {
            "marketplace_score": round(marketplace_score, 2),
            "strong_hits": strong_hits,
            "soft_hits": soft_hits,
            "single_brand_hits": single_brand_hits,
            "product_brand_count": product_brand_count,
        }

    model_type = _classify_business_model(
        aggregate_text,
        ships_worldwide=ships_worldwide,
        market_count=market_count,
    )
    return model_type, {
        "marketplace_score": round(marketplace_score, 2),
        "strong_hits": strong_hits,
        "soft_hits": soft_hits,
        "single_brand_hits": single_brand_hits,
        "product_brand_count": product_brand_count,
    }


def build_brand_market_profile(seed_crawl: CrawlResult, max_pages: int = 8) -> dict:
    """
    Build a lightweight market profile from multi-page crawling and hard signals.
    """
    if not seed_crawl.ok:
        return {
            "top_market": None,
            "top_market_confidence": 0.0,
            "country_scores": [],
            "model_type": "local_d2c",
            "signals": {"pages_crawled": []},
        }

    page_urls = _pick_market_pages(seed_crawl, max_pages=max_pages)
    crawls: list[CrawlResult] = [seed_crawl]
    crawled_urls = {seed_crawl.url.rstrip("/")}

    for url in page_urls[1:]:
        if url.rstrip("/") in crawled_urls:
            continue
        c = crawl_page(url)
        if c.ok:
            crawls.append(c)
            crawled_urls.add(url.rstrip("/"))

    signals = {
        "addresses": [],
        "address_snippets": [],
        "phone_prefixes": [],
        "currencies": [],
        "shipping": {"shipping_policy_found": False, "ships_worldwide": False},
        "payment_methods": [],
        "domain_hints": [],
        "language_hints": [],
        "timezone_hints": [],
        "pages_crawled": [c.url for c in crawls],
    }

    scores: dict[str, float] = {}
    all_text_chunks: list[str] = []
    product_brands: set[str] = set()

    def add_score(market: str | None, weight: float):
        normalized = _normalize_market_label(market)
        if not normalized:
            return
        scores[normalized] = scores.get(normalized, 0.0) + weight

    domain_hint = _extract_domain_hint(seed_crawl.url)
    if domain_hint:
        signals["domain_hints"].append(domain_hint)
        add_score(domain_hint, 1.4)

    for c in crawls:
        soup = c.soup
        text = c.text or ""
        signal_text = _build_signal_text(c)
        all_text_chunks.append(signal_text[:8000] or text[:6000])

        lang = ""
        if soup and soup.html and soup.html.get("lang"):
            lang = str(soup.html.get("lang"))
        lang_hint = _extract_language_hint(lang)
        if lang_hint and lang_hint not in signals["language_hints"]:
            signals["language_hints"].append(lang_hint)

        tz_hints = _extract_timezone_hints(signal_text)
        for tz in tz_hints:
            if tz not in signals["timezone_hints"]:
                signals["timezone_hints"].append(tz)

        for country in _extract_address_countries_from_jsonld(c.html or ""):
            normalized = _normalize_market_label(country)
            if normalized and normalized not in signals["addresses"]:
                signals["addresses"].append(normalized)
            add_score(country, 6.0)

        for brand in _extract_product_brands_from_jsonld(c.html or ""):
            key = re.sub(r"\s+", " ", brand).strip().lower()
            if key:
                product_brands.add(key)

        for snippet in _extract_address_snippets(signal_text[:16000]):
            if snippet not in signals["address_snippets"]:
                signals["address_snippets"].append(snippet)
            add_score(_infer_market_from_address_snippet(snippet), 4.5)

        phone_prefixes = _extract_phone_prefixes(signal_text[:12000])
        for p in phone_prefixes:
            if p not in signals["phone_prefixes"]:
                signals["phone_prefixes"].append(p)
            add_score(PHONE_MARKET_HINTS.get(p), 2.2)

        currencies = _extract_currency_hints(signal_text[:14000])
        for cur in currencies:
            if cur not in signals["currencies"]:
                signals["currencies"].append(cur)
            add_score(CURRENCY_MARKET_HINTS.get(cur), 1.8)

        shipping = _extract_shipping_hints(signal_text[:14000])
        signals["shipping"]["shipping_policy_found"] = (
            signals["shipping"]["shipping_policy_found"] or shipping["shipping_policy_found"]
        )
        signals["shipping"]["ships_worldwide"] = (
            signals["shipping"]["ships_worldwide"] or shipping["ships_worldwide"]
        )

        payments = _extract_payment_hints(signal_text[:14000])
        for p in payments:
            if p not in signals["payment_methods"]:
                signals["payment_methods"].append(p)

    country_scores = _to_probabilities(scores)
    top_market = country_scores[0]["market"] if country_scores else None
    top_market_confidence = country_scores[0]["probability"] if country_scores else 0.0

    aggregate_text = " ".join(all_text_chunks)
    model_type, model_details = _classify_business_model_v2(
        aggregate_text,
        ships_worldwide=bool(signals["shipping"]["ships_worldwide"]),
        market_count=len(country_scores),
        product_brand_count=len(product_brands),
    )

    profile = {
        "top_market": top_market,
        "top_market_confidence": round(top_market_confidence, 4),
        "country_scores": country_scores,
        "model_type": model_type,
        "model_details": model_details,
        "signals": signals,
    }
    logger.info(
        "Market profile built: top_market=%s conf=%.3f model=%s pages=%d",
        profile["top_market"],
        profile["top_market_confidence"],
        profile["model_type"],
        len(signals["pages_crawled"]),
    )
    return profile
