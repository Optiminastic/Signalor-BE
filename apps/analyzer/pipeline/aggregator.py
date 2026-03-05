import logging

from .utils import safe_score

logger = logging.getLogger("apps")

# Default weights — content + schema = 20%, the rest = 80%
# The 4 actionable pillars (eeat, technical, entity, ai_visibility)
# carry the most weight because users can take direct action on them.
DEFAULT_WEIGHTS = {
    "content": 0.10,
    "schema": 0.10,
    "eeat": 0.22,
    "technical": 0.20,
    "entity": 0.20,
    "ai_visibility": 0.18,
}

# Industry-specific weight overrides
# content + schema always sum to 0.20; actionable pillars get the 0.80
INDUSTRY_WEIGHTS = {
    "health": {
        "content": 0.10,
        "schema": 0.10,
        "eeat": 0.30,      # Health content NEEDS trust signals
        "technical": 0.15,
        "entity": 0.15,
        "ai_visibility": 0.20,
    },
    "medical": {
        "content": 0.10,
        "schema": 0.10,
        "eeat": 0.30,
        "technical": 0.15,
        "entity": 0.15,
        "ai_visibility": 0.20,
    },
    "finance": {
        "content": 0.10,
        "schema": 0.10,
        "eeat": 0.28,      # Financial content needs authority
        "technical": 0.15,
        "entity": 0.22,
        "ai_visibility": 0.15,
    },
    "ecommerce": {
        "content": 0.10,
        "schema": 0.10,
        "eeat": 0.15,
        "technical": 0.20,
        "entity": 0.20,
        "ai_visibility": 0.25,
    },
    "saas": {
        "content": 0.10,
        "schema": 0.10,
        "eeat": 0.18,
        "technical": 0.20,
        "entity": 0.17,
        "ai_visibility": 0.25,   # AI visibility matters most for SaaS discovery
    },
    "legal": {
        "content": 0.10,
        "schema": 0.10,
        "eeat": 0.30,
        "technical": 0.15,
        "entity": 0.15,
        "ai_visibility": 0.20,
    },
    "education": {
        "content": 0.10,
        "schema": 0.10,
        "eeat": 0.25,
        "technical": 0.15,
        "entity": 0.20,
        "ai_visibility": 0.20,
    },
    "news": {
        "content": 0.10,
        "schema": 0.10,
        "eeat": 0.25,
        "technical": 0.15,
        "entity": 0.15,
        "ai_visibility": 0.25,
    },
    "local_business": {
        "content": 0.10,
        "schema": 0.10,
        "eeat": 0.18,
        "technical": 0.17,
        "entity": 0.25,    # Local businesses need strong entity presence
        "ai_visibility": 0.20,
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
