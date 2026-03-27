import json
import logging
import re
from urllib.parse import urlparse

import requests

from .crawler import CrawlResult
from .utils import extract_brand_name, safe_score

logger = logging.getLogger("apps")

SOCIAL_DOMAINS = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "youtube.com", "github.com", "tiktok.com",
}

COMMUNITY_DOMAINS = {
    "reddit.com": "reddit",
    "medium.com": "medium",
}

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"


def _check_wikipedia(brand_name: str) -> bool:
    try:
        resp = requests.get(
            WIKIPEDIA_API,
            params={
                "action": "query",
                "list": "search",
                "srsearch": brand_name,
                "srlimit": 3,
                "format": "json",
            },
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("query", {}).get("search", [])
            for r in results:
                title_lower = r.get("title", "").lower()
                snippet_lower = r.get("snippet", "").lower()
                if brand_name.lower() in title_lower:
                    # Skip disambiguation pages
                    if "(disambiguation)" in title_lower:
                        continue
                    # Skip if snippet is clearly about something else
                    if snippet_lower and len(snippet_lower) > 10:
                        return True
                    elif not snippet_lower:
                        return True
    except Exception as exc:
        logger.warning("Wikipedia check failed for %s: %s", brand_name, exc)
    return False


def _llm_available() -> bool:
    """Check if any LLM is available (OpenRouter or direct Gemini)."""
    from .llm import is_available
    return is_available()


def _check_knowledge_panel(brand_name: str, industry: str) -> tuple[bool, float]:
    """Use LLM to check if brand has a knowledge panel / is well-known."""
    try:
        from .llm import ask_llm

        prompt = (
            f"Is '{brand_name}' a well-known brand/company in the {industry or 'technology'} industry? "
            f"Does it have a Google Knowledge Panel? "
            f"Reply with JSON: {{\"well_known\": true/false, \"confidence\": 0.0-1.0, \"description\": \"brief\"}}"
        )
        text = ask_llm(prompt, preferred_provider="gemini", max_tokens=512, purpose="Knowledge Panel Check")
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data.get("well_known", False), data.get("confidence", 0.0)
    except Exception as exc:
        logger.warning("Knowledge panel check failed: %s", exc)
    return False, 0.0


def _check_third_party_mentions(brand_name: str) -> tuple[int, float]:
    """Use LLM to estimate third-party mentions."""
    try:
        from .llm import ask_llm

        prompt = (
            f"How often is '{brand_name}' mentioned in third-party publications, review sites, "
            f"and industry directories? Rate from 0-10. "
            f"Reply with JSON: {{\"mention_score\": 0-10, \"confidence\": 0.0-1.0}}"
        )
        text = ask_llm(prompt, preferred_provider="gemini", max_tokens=512, purpose="Third-Party Mentions")
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data.get("mention_score", 0), data.get("confidence", 0.0)
    except Exception as exc:
        logger.warning("Third-party check failed: %s", exc)
    return 0, 0.0


def _static_entity_signals(soup, crawl_url: str) -> tuple[float, dict]:
    """Score entity authority using only static HTML signals (no LLM needed)."""
    details = {}
    score = 0.0

    # Social media links (15 pts — boosted since we can't use Gemini)
    social_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        try:
            domain = urlparse(href).netloc.lower()
            for sd in SOCIAL_DOMAINS:
                if domain.endswith(sd):
                    social_links.append(sd)
                    break
        except Exception:
            continue
    unique_socials = set(social_links)
    details["social_profiles"] = list(unique_socials)
    details["social_count"] = len(unique_socials)
    if len(unique_socials) >= 3:
        score += 15
    elif len(unique_socials) >= 2:
        score += 10
    elif len(unique_socials) == 1:
        score += 5

    # Organization schema present (10 pts)
    import json as json_mod
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json_mod.loads(script.string or "")
            schemas = data if isinstance(data, list) else [data]
            for s in schemas:
                types = s.get("@type", "")
                if isinstance(types, str):
                    types = [types]
                if "Organization" in types:
                    score += 10
                    details["org_schema_present"] = True
                    # Check sameAs (social links in schema)
                    same_as = s.get("sameAs", [])
                    if same_as:
                        score += 5
                        details["schema_same_as"] = len(same_as) if isinstance(same_as, list) else 1
                    break
                # Also check @graph
                for item in s.get("@graph", []):
                    if isinstance(item, dict):
                        t = item.get("@type", "")
                        if t == "Organization" or (isinstance(t, list) and "Organization" in t):
                            score += 10
                            details["org_schema_present"] = True
                            same_as = item.get("sameAs", [])
                            if same_as:
                                score += 5
                                details["schema_same_as"] = len(same_as) if isinstance(same_as, list) else 1
                            break
        except (json_mod.JSONDecodeError, TypeError):
            continue

    # Contact info present (10 pts)
    contact_patterns = [
        r"contact", r"email", r"phone", r"tel:", r"mailto:",
    ]
    html_lower = str(soup).lower()
    contact_found = sum(1 for p in contact_patterns if p in html_lower)
    if contact_found >= 2:
        score += 10
        details["contact_info"] = True
    else:
        details["contact_info"] = False

    # Domain legitimacy (10 pts)
    domain = urlparse(crawl_url).netloc.replace("www.", "")
    details["domain"] = domain
    # Check: not an IP address, has a recognized TLD, reasonable length
    is_ip = bool(re.match(r"^\d{1,3}(\.\d{1,3}){3}$", domain))
    parts = domain.split(".")
    has_tld = len(parts) >= 2 and 2 <= len(parts[-1]) <= 6
    reasonable_length = len(domain) <= 50
    domain_ok = not is_ip and has_tld and reasonable_length
    details["domain_legitimate"] = domain_ok
    if domain_ok:
        score += 10

    return score, details


def score_entity(crawl: CrawlResult, industry: str = "", override_brand: str = "") -> tuple[float, dict]:
    if not crawl.ok:
        return 0.0, {"error": crawl.error}

    soup = crawl.soup
    details = {"checks": {}, "findings": []}
    score = 0.0

    brand_name = override_brand or extract_brand_name(soup, crawl.url)
    details["checks"]["brand_name"] = brand_name

    # Brand extraction (5 pts)
    if brand_name:
        score += 5
        details["checks"]["brand_extracted"] = True
    else:
        details["checks"]["brand_extracted"] = False

    # Wikipedia API check (25 pts) — always works, no Gemini needed
    has_wiki = _check_wikipedia(brand_name)
    details["checks"]["wikipedia_presence"] = has_wiki
    if has_wiki:
        score += 25
    else:
        details["findings"].append("no_wikipedia_presence")

    # Check if LLM is available
    use_llm = _llm_available()
    details["checks"]["llm_available"] = use_llm

    if use_llm:
        # Knowledge Panel via LLM (25 pts)
        well_known, kp_confidence = _check_knowledge_panel(brand_name, industry)
        details["checks"]["knowledge_panel"] = well_known
        details["checks"]["kp_confidence"] = kp_confidence
        if well_known:
            score += 25
        else:
            details["findings"].append("brand_not_in_ai")

        # Third-party mentions via Gemini (25 pts)
        mention_score, mention_confidence = _check_third_party_mentions(brand_name)
        details["checks"]["third_party_score"] = mention_score
        details["checks"]["mention_confidence"] = mention_confidence
        tp_points = min(25, mention_score * 2.5)
        score += tp_points

        # Social media links (10 pts)
        social_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            try:
                domain = urlparse(href).netloc.lower()
                for sd in SOCIAL_DOMAINS:
                    if domain.endswith(sd):
                        social_links.append(sd)
                        break
            except Exception:
                continue
        unique_socials = set(social_links)
        details["checks"]["social_profiles"] = list(unique_socials)
        details["checks"]["social_count"] = len(unique_socials)
        if len(unique_socials) >= 2:
            score += 10
        elif len(unique_socials) == 1:
            score += 5
        else:
            details["findings"].append("no_social_profiles")

        # Brand name coherence — brand name appears in title/H1 (10 pts)
        domain = urlparse(crawl.url).netloc
        details["checks"]["domain"] = domain
        brand_in_title = False
        brand_lower = brand_name.lower()
        page_title = soup.find("title")
        if page_title and brand_lower in page_title.get_text(strip=True).lower():
            brand_in_title = True
        if not brand_in_title:
            og_title = soup.find("meta", property="og:title")
            if og_title and brand_lower in (og_title.get("content", "")).lower():
                brand_in_title = True
        if not brand_in_title:
            h1 = soup.find("h1")
            if h1 and brand_lower in h1.get_text(strip=True).lower():
                brand_in_title = True
        details["checks"]["brand_in_identity"] = brand_in_title
        if brand_in_title:
            score += 10
        else:
            details["findings"].append("brand_not_in_title")

    else:
        # FALLBACK: Score using only static signals (no Gemini)
        # Redistribute points to static checks so score isn't artificially 0
        details["checks"]["scoring_mode"] = "static_fallback"

        static_score, static_details = _static_entity_signals(soup, crawl.url)
        details["checks"].update(static_details)

        # Wikipedia (already scored above) + static signals
        # Scale static score to fill the 65pts that Gemini would have covered
        # Static max is ~50pts, so scale to 65
        scaled_static = (static_score / 50.0) * 65.0 if static_score > 0 else 0
        score += scaled_static

        if not static_details.get("social_profiles"):
            details["findings"].append("no_social_profiles")

    # Community presence check (Reddit & Medium links/mentions)
    community_links = {"reddit": False, "medium": False}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        try:
            domain = urlparse(href).netloc.lower()
            for cd, key in COMMUNITY_DOMAINS.items():
                if domain.endswith(cd):
                    community_links[key] = True
        except Exception:
            continue
    details["checks"]["community_links"] = community_links
    if not community_links["reddit"]:
        details["findings"].append("no_reddit_presence")
    if not community_links["medium"]:
        details["findings"].append("no_medium_presence")

    # Entity collision confidence — reduce score if brand name collides with known entity
    from .utils import check_entity_collision
    collision, known = check_entity_collision(brand_name)
    if collision:
        # Apply confidence multiplier: 0.3 for ambiguous, 0.0 for confirmed collision
        # LLM-dependent scores (wiki, knowledge panel, third-party) are most affected
        confidence = 0.3  # Assume ambiguous unless we can confirm
        raw_score = score
        score = score * confidence
        details["checks"]["entity_collision"] = True
        details["checks"]["collision_entity"] = known["entity"]
        details["checks"]["collision_confidence"] = confidence
        details["checks"]["raw_entity_score"] = raw_score
        logger.info("Entity collision: '%s' vs '%s' — score %.1f → %.1f (confidence=%.1f)",
                     brand_name, known["entity"], raw_score, score, confidence)

    score = safe_score(score)
    details["score"] = score
    return score, details
