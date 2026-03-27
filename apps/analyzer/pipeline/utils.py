import re
import logging
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger("apps")


def extract_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def count_words(text: str) -> int:
    return len(text.split())


def extract_internal_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    parsed_base = urlparse(base_url)
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        if parsed.netloc == parsed_base.netloc and parsed.scheme in ("http", "https"):
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            links.add(clean.rstrip("/"))
    return list(links)


def extract_domain(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def extract_brand_name(soup: BeautifulSoup, url: str) -> str:
    og_site = soup.find("meta", property="og:site_name")
    if og_site and og_site.get("content"):
        name = og_site["content"].strip()
        if len(name) <= 40:
            return name

    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        parts = re.split(r"[|\-–—:]", title_tag.string)
        # Try each part — prefer short ones that look like brand names
        candidates = [p.strip() for p in parts if p.strip()]
        # If multiple parts, pick the shortest (likely the brand, not tagline)
        if len(candidates) > 1:
            short = min(candidates, key=len)
            if len(short) <= 30:
                return short
        # Single part — only use if it's short enough to be a brand name
        if candidates and len(candidates[0]) <= 30:
            return candidates[0]

    # Fallback: pick the most meaningful domain part (skip generic TLDs and common subdomains)
    _SKIP = {"www", "com", "net", "org", "io", "co", "uk", "de", "fr", "app", "ai", "dev", "ui", "api", "get", "try"}
    domain = extract_domain(url)
    parts = [p for p in domain.split(".") if len(p) >= 3 and p.lower() not in _SKIP]
    if parts:
        # Prefer longest (most specific/unique) part — e.g. "aceternity" from "ui.aceternity.com"
        return max(parts, key=len).capitalize()
    return domain.split(".")[0].capitalize()


def safe_score(value: float, max_val: float = 100.0) -> float:
    return max(0.0, min(float(value), max_val))


# ── Brand Entity Collision Detection ──────────────────────────────────────

KNOWN_ENTITIES = {
    "arkit": {"entity": "Apple ARKit", "category": "augmented reality framework", "keywords": ["apple", "ios", "ar", "augmented reality", "sdk", "framework"]},
    "swift": {"entity": "Swift programming language", "category": "programming language", "keywords": ["apple", "ios", "programming", "language", "xcode"]},
    "react": {"entity": "React.js", "category": "javascript framework", "keywords": ["facebook", "meta", "javascript", "library", "frontend"]},
    "flutter": {"entity": "Flutter framework", "category": "mobile development framework", "keywords": ["google", "dart", "mobile", "cross-platform", "sdk"]},
    "unity": {"entity": "Unity game engine", "category": "game development engine", "keywords": ["game", "engine", "3d", "gamedev", "unreal"]},
    "spark": {"entity": "Apache Spark", "category": "data processing framework", "keywords": ["apache", "hadoop", "data", "big data", "processing"]},
    "nest": {"entity": "Nest.js or Google Nest", "category": "framework or smart home", "keywords": ["google", "thermostat", "smart home", "node", "backend"]},
    "vue": {"entity": "Vue.js", "category": "javascript framework", "keywords": ["javascript", "frontend", "framework", "nuxt"]},
    "angular": {"entity": "Angular framework", "category": "javascript framework", "keywords": ["google", "typescript", "frontend", "framework"]},
    "notion": {"entity": "Notion app", "category": "productivity software", "keywords": ["productivity", "workspace", "notes", "wiki"]},
    "figma": {"entity": "Figma design tool", "category": "design software", "keywords": ["design", "ui", "ux", "prototype"]},
    "stripe": {"entity": "Stripe payments", "category": "payment processing", "keywords": ["payments", "billing", "api", "fintech"]},
}


def check_entity_collision(brand_name: str, domain: str = "", industry: str = "") -> tuple[bool, dict | None]:
    """
    Check if a brand name collides with a well-known entity.
    Returns (has_collision, entity_info) or (False, None).
    """
    key = brand_name.lower().strip()
    if key in KNOWN_ENTITIES:
        return True, KNOWN_ENTITIES[key]
    # Also check without common suffixes
    for suffix in (" inc", " llc", " ltd", " corp", " co"):
        stripped = key.replace(suffix, "").strip()
        if stripped in KNOWN_ENTITIES:
            return True, KNOWN_ENTITIES[stripped]
    return False, None


def compute_entity_confidence(brand_name: str, text: str, domain: str = "", industry: str = "") -> float:
    """
    Compute confidence (0.0–1.0) that a mention is about the actual brand, not a collision entity.
    """
    collision, known = check_entity_collision(brand_name, domain, industry)
    if not collision:
        return 1.0  # No collision risk

    text_lower = text.lower()
    entity_name = known["entity"].lower()
    keywords = known.get("keywords", [])

    # Check how many collision keywords appear near the brand mention
    collision_hits = sum(1 for kw in keywords if kw in text_lower)

    # Check if the known entity name appears
    if entity_name in text_lower:
        return 0.0  # Confirmed wrong entity

    if collision_hits >= 3:
        return 0.1  # Very likely wrong entity
    if collision_hits >= 2:
        return 0.3  # Probably wrong entity
    if collision_hits >= 1:
        return 0.5  # Ambiguous

    # Check if domain appears (confirms right entity)
    if domain and domain.lower() in text_lower:
        return 0.9  # Domain mentioned — likely correct

    return 0.6  # No collision signals but no confirmation either
