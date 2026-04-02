import logging

from .utils import safe_score

logger = logging.getLogger("apps")

# Weights v3: Content + E-E-A-T = 50% (trust pillars)
# Technical = 15%, Schema = 10%, Entity = 15%, AI Visibility = 10%
DEFAULT_WEIGHTS = {
    "content": 0.25,
    "schema": 0.10,
    "eeat": 0.25,
    "technical": 0.15,
    "entity": 0.15,
    "ai_visibility": 0.10,
}

INDUSTRY_WEIGHTS = {
    "health": {
        "content": 0.18, "schema": 0.10, "eeat": 0.30,
        "technical": 0.14, "entity": 0.13, "ai_visibility": 0.15,
    },
    "medical": {
        "content": 0.18, "schema": 0.10, "eeat": 0.30,
        "technical": 0.14, "entity": 0.13, "ai_visibility": 0.15,
    },
    "finance": {
        "content": 0.18, "schema": 0.10, "eeat": 0.28,
        "technical": 0.14, "entity": 0.15, "ai_visibility": 0.15,
    },
    "ecommerce": {
        "content": 0.20, "schema": 0.10, "eeat": 0.20,
        "technical": 0.15, "entity": 0.15, "ai_visibility": 0.20,
    },
    "saas": {
        "content": 0.20, "schema": 0.10, "eeat": 0.22,
        "technical": 0.13, "entity": 0.15, "ai_visibility": 0.20,
    },
    "legal": {
        "content": 0.18, "schema": 0.10, "eeat": 0.30,
        "technical": 0.14, "entity": 0.13, "ai_visibility": 0.15,
    },
    "education": {
        "content": 0.18, "schema": 0.10, "eeat": 0.28,
        "technical": 0.14, "entity": 0.15, "ai_visibility": 0.15,
    },
    "news": {
        "content": 0.18, "schema": 0.10, "eeat": 0.25,
        "technical": 0.14, "entity": 0.15, "ai_visibility": 0.18,
    },
    "local_business": {
        "content": 0.18, "schema": 0.10, "eeat": 0.22,
        "technical": 0.15, "entity": 0.20, "ai_visibility": 0.15,
    },
}


def detect_industry(soup, text: str = "") -> str:
    """Detect the industry from page content using heuristics."""
    signals = []

    # Check meta keywords
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw and meta_kw.get("content"):
        signals.append(meta_kw["content"].lower())

    # Check meta description
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        signals.append(meta_desc["content"].lower())

    # Check title
    title = soup.find("title")
    if title and title.string:
        signals.append(title.string.lower())

    # Check body text
    signals.append(text[:1000].lower())

    combined = " ".join(signals)

    # Industry detection — score all industries, return best match
    # Use minimum_matches threshold: specific industries need fewer hits to win
    rules = [
        # (industry, keywords, min_matches, weight)
        # Specific/distinctive keywords carry more weight
        ("health",        ["health", "medical", "doctor", "patient", "clinic", "hospital", "wellness", "therapy", "pharmaceutical"], 2, 1.5),
        ("medical",       ["diagnosis", "treatment", "symptom", "medication", "surgery", "prescription", "physician"], 2, 1.5),
        ("finance",       ["finance", "banking", "investment", "loan", "mortgage", "insurance", "trading", "fintech", "crypto", "portfolio"], 2, 1.3),
        ("legal",         ["lawyer", "attorney", "legal", "law firm", "litigation", "court", "jurisdiction", "compliance"], 2, 1.3),
        ("ecommerce",     ["shop", "store", "add to cart", "checkout", "shipping", "ecommerce", "e-commerce", "marketplace", "buy now"], 2, 1.2),
        ("saas",          ["saas", "subscription", "free trial", "pricing plan", "per month", "sign up for free", "dashboard", "integrations"], 2, 1.0),
        ("education",     ["course", "curriculum", "enrollment", "certificate", "university", "school", "learning management", "quiz", "lecture"], 2, 1.2),
        ("news",          ["journalist", "breaking news", "editorial", "newsroom", "wire service", "byline"], 2, 1.5),
        ("local_business", ["near me", "visit us", "store hours", "book appointment", "our location", "local delivery"], 2, 1.2),
    ]

    scores = {}
    for industry, keywords, min_matches, weight in rules:
        match_count = sum(1 for kw in keywords if kw in combined)
        if match_count >= min_matches:
            scores[industry] = match_count * weight

    if scores:
        return max(scores, key=scores.get)

    return "default"


def get_weights(industry: str) -> dict[str, float]:
    """Get scoring weights for a given industry."""
    return INDUSTRY_WEIGHTS.get(industry, DEFAULT_WEIGHTS)


def compute_composite(
    content: float,
    schema: float,
    eeat: float,
    technical: float,
    entity: float = 0.0,
    ai_visibility: float = 0.0,
    industry: str = "default",
) -> float:
    weights = get_weights(industry)
    composite = (
        content * weights["content"]
        + schema * weights["schema"]
        + eeat * weights["eeat"]
        + technical * weights["technical"]
        + entity * weights["entity"]
        + ai_visibility * weights["ai_visibility"]
    )

    # ── Smarter clamps v3 ──

    # If AI doesn't see you, you don't exist
    if ai_visibility < 20 and entity < 20:
        composite = min(composite, 50.0)

    # Untrustworthy content can't score high
    if content < 15 and eeat < 15:
        composite = min(composite, 35.0)

    # Bad infrastructure tanks everything
    if technical < 30:
        composite = min(composite, 40.0)

    # Technical floor: technically sound sites shouldn't score dead
    if technical > 50 and content < 10:
        composite = max(composite, 15.0)

    return safe_score(composite)


def compute_static_composite(
    content: float,
    schema: float,
    eeat: float,
    technical: float,
    industry: str = "default",
) -> float:
    """Compute composite using only static pillars (for competitor scoring)."""
    weights = get_weights(industry)
    static_weights = {
        "content": weights["content"],
        "schema": weights["schema"],
        "eeat": weights["eeat"],
        "technical": weights["technical"],
    }
    total_weight = sum(static_weights.values())
    composite = (
        content * static_weights["content"]
        + schema * static_weights["schema"]
        + eeat * static_weights["eeat"]
        + technical * static_weights["technical"]
    ) / total_weight
    return safe_score(composite)
