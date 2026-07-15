import logging
import re
from urllib.parse import urlparse

import requests

from .crawler import CrawlResult
from .utils import extract_brand_name, safe_score

logger = logging.getLogger("apps")

SOCIAL_DOMAINS = {
    "linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "github.com",
    "tiktok.com",
}

COMMUNITY_DOMAINS = {
    "reddit.com": "reddit",
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


def _search_available() -> bool:
    """Real search backend (Serper) available? Entity authority is measured, not guessed."""
    from . import serper

    return serper.is_configured()


def _brand_search_signals(brand_name: str, own_domain: str = "") -> tuple[bool | None, int | None]:
    """One real Google search -> (has_knowledge_panel, third_party_mention_count).

    Epic 8: these two signals used to be produced by asking an LLM whether a brand "has a
    Google Knowledge Panel" and to "rate 0-10 how often it is mentioned" -- facts no model
    can know, yet they were worth up to 50 entity points. Both now come from observed
    Serper data, and a single search yields both (``knowledgeGraph`` + ``organic``), so a
    brand costs one search rather than two.

    Returns ``(None, None)`` when Serper is unavailable: **unknown**, never guessed. The
    caller must award no points for an unknown.
    """
    from . import serper

    data = serper.search(brand_name, num=10)
    if data is None:
        return None, None

    has_panel = bool((data.get("knowledgeGraph") or {}).get("title"))

    own = (own_domain or "").lower().removeprefix("www.")
    brand_lower = (brand_name or "").lower()
    mentions = 0
    for item in (data.get("organic") or [])[:10]:
        host = urlparse(item.get("link") or "").netloc.lower().removeprefix("www.")
        if not host or (own and (host == own or host.endswith("." + own))):
            continue  # the brand's own site is not a third-party mention
        blob = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
        if brand_lower and brand_lower in blob:
            mentions += 1
    return has_panel, mentions


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
        r"contact",
        r"email",
        r"phone",
        r"tel:",
        r"mailto:",
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

    # Real search signals (Serper). Gating on the search backend -- not on an LLM --
    # is the Epic 8 fix: authority is measured, or it is unknown. It is never invented.
    use_search = _search_available()
    details["checks"]["search_available"] = use_search

    if use_search:
        # One search yields both the knowledge panel and third-party mentions.
        has_panel, mention_score = _brand_search_signals(brand_name, urlparse(crawl.url).netloc)

        # Knowledge Panel — observed in Google's knowledgeGraph (25 pts)
        details["checks"]["knowledge_panel"] = has_panel
        # Confidence is 1.0 for an observed fact, 0.0 when the lookup itself failed.
        details["checks"]["kp_confidence"] = 0.0 if has_panel is None else 1.0
        if has_panel:
            score += 25
        elif has_panel is False:
            details["findings"].append("brand_not_in_ai")
        # has_panel is None -> lookup failed: unknown, no points, no finding.

        # Third-party mentions — real organic results off the brand's own domain (25 pts)
        details["checks"]["third_party_mentions"] = mention_score
        details["checks"]["mention_confidence"] = 0.0 if mention_score is None else 1.0
        if mention_score is not None:
            score += min(25, mention_score * 2.5)

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
        # FALLBACK: no search backend configured -- score from static, observable signals
        # only. Redistribute points so the score isn't artificially 0 (we simply cannot
        # measure off-site authority without a search API).
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

    # Community presence check (Reddit links/mentions)
    community_links = {"reddit": False}
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

    # Entity collision confidence — reduce score if brand name collides with known entity
    from .utils import check_entity_collision

    collision, known = check_entity_collision(brand_name)
    if collision:
        # Apply confidence multiplier: 0.3 for ambiguous, 0.0 for confirmed collision
        # Off-site scores (wiki, knowledge panel, third-party) are most affected
        confidence = 0.3  # Assume ambiguous unless we can confirm
        raw_score = score
        score = score * confidence
        details["checks"]["entity_collision"] = True
        details["checks"]["collision_entity"] = known["entity"]
        details["checks"]["collision_confidence"] = confidence
        details["checks"]["raw_entity_score"] = raw_score
        logger.info(
            "Entity collision: '%s' vs '%s' — score %.1f → %.1f (confidence=%.1f)",
            brand_name,
            known["entity"],
            raw_score,
            score,
            confidence,
        )

    score = safe_score(score)
    details["score"] = score
    return score, details
