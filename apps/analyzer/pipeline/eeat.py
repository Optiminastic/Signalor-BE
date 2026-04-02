"""
E-E-A-T Scorer v3 — Believability Score.

Question: "Would an AI believe this author and trust this content?"

Score = (Identity Strength × 0.25) + (Evidence Strength × 0.35) +
        (Experience Signals × 0.25) + (Trust Infrastructure × 0.15)
"""
import json
import logging
import re
from urllib.parse import urlparse

from .crawler import CrawlResult
from .utils import safe_score

logger = logging.getLogger("apps")

TRUST_TLDS = {".gov", ".edu", ".ac.uk", ".gov.uk", ".gov.au", ".edu.au"}

TRUST_DOMAINS = {
    "wikipedia.org", "bbc.com", "nytimes.com", "reuters.com",
    "nature.com", "pubmed.ncbi.nlm.nih.gov", "scholar.google.com",
    "forbes.com", "hbr.org", "techcrunch.com", "wsj.com",
    "theguardian.com", "washingtonpost.com", "bloomberg.com",
    "sciencedirect.com", "springer.com", "arxiv.org", "ieee.org",
    "who.int", "cdc.gov", "nih.gov",
    "harvard.edu", "mit.edu", "stanford.edu",
}


def _is_trust_link(href: str) -> bool:
    try:
        domain = urlparse(href).netloc.lower()
        for d in TRUST_DOMAINS:
            if domain.endswith(d):
                return True
        for tld in TRUST_TLDS:
            if domain.endswith(tld):
                return True
    except Exception:
        pass
    return False


# ── 1. Identity Strength (25 pts) ─────────────────────────────────────────

def _score_identity(crawl: CrawlResult) -> tuple[float, dict]:
    """
    Does a real, verifiable person/org stand behind this content?
    Not just "author exists" — is the author credible?
    """
    soup = crawl.soup
    html_lower = crawl.html.lower()
    details = {}
    score = 0.0

    # 1a. Author attribution (8 pts) — name found anywhere
    author_found = False
    author_name = ""

    # Meta tag
    author_meta = soup.find("meta", attrs={"name": "author"})
    if author_meta and author_meta.get("content"):
        author_found = True
        author_name = author_meta["content"]

    # HTML class patterns
    if not author_found:
        for cls in ["author", "byline", "writer", "post-author", "entry-author"]:
            el = soup.find(class_=re.compile(cls, re.I))
            if el and el.get_text(strip=True):
                author_found = True
                author_name = el.get_text(strip=True)
                break

    # JSON-LD
    if not author_found:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    for obj in [item] + item.get("@graph", []):
                        author = obj.get("author") if isinstance(obj, dict) else None
                        if isinstance(author, dict) and author.get("name"):
                            author_found = True
                            author_name = author["name"]
                            break
                        if isinstance(author, list) and author:
                            a = author[0]
                            author_found = True
                            author_name = a.get("name", str(a)) if isinstance(a, dict) else str(a)
                            break
            except (json.JSONDecodeError, TypeError):
                continue

    details["author_found"] = author_found
    details["author_name"] = author_name
    if author_found:
        score += 8
    else:
        details["_finding_author"] = "no_author"

    # 1b. Author bio with credentials (8 pts)
    bio_found = False
    for cls in ["author-bio", "author-description", "bio", "about-author", "author-info"]:
        el = soup.find(class_=re.compile(cls, re.I))
        if el and len(el.get_text(strip=True)) > 30:
            bio_found = True
            break
    details["author_bio"] = bio_found
    if bio_found:
        score += 8
    elif author_found:
        score += 2  # Author exists but no bio
    else:
        details["_finding_bio"] = "no_author_bio"

    # 1c. Author has external presence — sameAs, LinkedIn, social links (5 pts)
    has_social = False
    social_patterns = ["linkedin.com", "twitter.com", "x.com", "github.com", "medium.com"]
    # Check JSON-LD sameAs
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                for obj in [item] + item.get("@graph", []):
                    same_as = obj.get("sameAs", []) if isinstance(obj, dict) else []
                    if isinstance(same_as, str):
                        same_as = [same_as]
                    for link in same_as:
                        if any(s in str(link).lower() for s in social_patterns):
                            has_social = True
                            break
        except (json.JSONDecodeError, TypeError):
            continue

    # Also check HTML links
    if not has_social:
        for a in soup.find_all("a", href=True):
            if any(s in a["href"].lower() for s in social_patterns):
                has_social = True
                break

    details["author_social_presence"] = has_social
    if has_social:
        score += 5

    # 1d. Organization info (4 pts)
    has_org = any(x in html_lower for x in ["about us", "our team", "our mission", "founded", "our story"])
    details["has_org_info"] = has_org
    if has_org:
        score += 4

    return min(score, 25), details


# ── 2. Evidence Strength (35 pts) ─────────────────────────────────────────

def _score_evidence(crawl: CrawlResult) -> tuple[float, dict]:
    """
    Does the content back up claims with real evidence?
    Citations, data, case studies, not just opinions.
    """
    soup = crawl.soup
    text = crawl.text
    text_lower = text.lower()
    html_lower = crawl.html.lower()
    details = {}
    score = 0.0

    # 2a. External citations to authoritative sources (10 pts)
    parsed_base = urlparse(crawl.url)
    external_links = []
    trust_links = []
    for a in soup.find_all("a", href=True):
        try:
            parsed = urlparse(a["href"])
            if parsed.netloc and parsed.netloc != parsed_base.netloc and parsed.scheme in ("http", "https", ""):
                external_links.append(a["href"])
                if _is_trust_link(a["href"]):
                    trust_links.append(a["href"])
        except Exception:
            continue

    details["external_link_count"] = len(external_links)
    details["trust_link_count"] = len(trust_links)

    if len(trust_links) >= 3:
        score += 10
    elif len(trust_links) >= 1:
        score += 6
    elif len(external_links) >= 3:
        score += 4
    elif len(external_links) >= 1:
        score += 2
    else:
        details["_finding_trust"] = "no_trust_links"

    # 2b. Source diversity (5 pts)
    ext_domains = set()
    for l in external_links:
        try:
            ext_domains.add(urlparse(l).netloc)
        except Exception:
            pass
    details["source_diversity"] = len(ext_domains)
    if len(ext_domains) >= 5:
        score += 5
    elif len(ext_domains) >= 3:
        score += 3
    else:
        details["_finding_diversity"] = "low_source_diversity"

    # 2c. Real data and statistics (10 pts)
    stat_patterns = [
        r"\d+(?:\.\d+)?%",
        r"\$\d[\d,.]*",
        r"\d[\d,.]*\s*(?:billion|million|thousand)",
        r"\b(?:increased?|decreased?|grew?|rose|fell)\s+(?:by\s+)?\d",
    ]
    stat_count = sum(len(re.findall(p, text, re.I)) for p in stat_patterns)
    details["statistic_count"] = stat_count

    if stat_count >= 8:
        score += 10
    elif stat_count >= 4:
        score += 7
    elif stat_count >= 2:
        score += 4
    elif stat_count >= 1:
        score += 2
    else:
        details["_finding_stats"] = "no_statistics"

    # 2d. Case studies / real examples (5 pts)
    case_study_signals = [
        r"\bcase study\b", r"\breal.world example\b", r"\bfor example\b",
        r"\bin practice\b", r"\bwe (found|discovered|observed|measured)\b",
        r"\bclient\b.*\b(saw|achieved|increased|improved)\b",
        r"\bbefore and after\b", r"\bresults showed\b",
    ]
    case_count = sum(1 for p in case_study_signals if re.search(p, text_lower))
    details["case_study_signals"] = case_count

    if case_count >= 3:
        score += 5
    elif case_count >= 1:
        score += 3

    # 2e. AI-style generic writing penalty (-5 pts)
    generic_patterns = [
        r"\bin today'?s (?:fast.?paced|ever.?changing|digital|modern) (?:world|landscape|era)\b",
        r"\b(?:unlock|unleash|harness) the (?:power|potential|full potential)\b",
        r"\btake (?:your|it) to the next level\b",
        r"\bgame.?changer\b",
        r"\bseamless(?:ly)?\b.*\bexperience\b",
    ]
    generic_count = sum(1 for p in generic_patterns if re.search(p, text_lower))
    details["generic_ai_phrases"] = generic_count

    if generic_count >= 3:
        score -= 5
        details["_finding_generic"] = "generic_ai_writing_detected"
    elif generic_count >= 2:
        score -= 2

    return max(0, min(score, 35)), details


# ── 3. Experience Signals (25 pts) ────────────────────────────────────────

def _score_experience(crawl: CrawlResult) -> tuple[float, dict]:
    """
    First-hand experience: "I tested this", "we built this", real numbers.
    The first E in E-E-A-T.
    """
    text_lower = crawl.text.lower()
    html_lower = crawl.html.lower()
    details = {}
    score = 0.0

    # 3a. First-person experience language (10 pts)
    experience_patterns = [
        r"\bi tested\b", r"\bi tried\b", r"\bi used\b", r"\bin my experience\b",
        r"\bwe (?:found|tested|built|implemented|analyzed|measured|ran|created)\b",
        r"\bour team\b", r"\bhands-on\b", r"\breal.?world\b",
        r"\bafter (?:implementing|testing|using|building)\b",
        r"\blessons? learned\b", r"\bwhat we learned\b",
    ]
    exp_count = sum(1 for p in experience_patterns if re.search(p, text_lower))
    details["experience_phrases"] = exp_count

    if exp_count >= 4:
        score += 10
    elif exp_count >= 2:
        score += 6
    elif exp_count >= 1:
        score += 3
    else:
        details["_finding_exp"] = "no_first_hand_experience"

    # 3b. Specific numbers and results (8 pts) — not vague claims
    specific_result_patterns = [
        r"\d+%\s+(?:increase|decrease|improvement|growth|reduction|boost)",
        r"(?:increased?|improved?|reduced?|grew?|boosted?)\s+(?:by\s+)?\d+",
        r"\d+\s+(?:clients?|customers?|users?|companies|projects?)",
        r"over\s+\d+\s+(?:years?|months?|weeks?)\b",
        r"\$\d[\d,.]*\s+in\s+(?:revenue|savings|sales|profit)",
    ]
    specific_count = sum(1 for p in specific_result_patterns if re.search(p, text_lower))
    details["specific_results"] = specific_count

    if specific_count >= 3:
        score += 8
    elif specific_count >= 1:
        score += 4

    # 3c. Date signals — publish + update (4 pts)
    soup = crawl.soup
    has_publish_date = bool(soup.find("time", attrs={"datetime": True}) or
                           soup.find("meta", property="article:published_time"))
    has_update_date = bool(soup.find("meta", property="article:modified_time"))
    details["publish_date"] = has_publish_date
    details["updated_date"] = has_update_date

    if has_publish_date:
        score += 2
    else:
        details["_finding_date"] = "no_publish_date"
    if has_update_date:
        score += 2

    # 3d. Expert quotes with attribution (3 pts)
    quote_patterns = [
        r"[\"'\u201C][\w\s]{15,}[\"'\u201D]\s*(?:,?\s*(?:says?|said|explains?|notes?|wrote))",
        r"[\"'\u201C][\w\s]{15,}[\"'\u201D]\s*[-\u2014]\s*\w",
    ]
    quote_count = sum(len(re.findall(p, crawl.text)) for p in quote_patterns)
    quote_count += len(crawl.soup.find_all("blockquote"))
    details["expert_quotes"] = quote_count

    if quote_count >= 2:
        score += 3
    elif quote_count >= 1:
        score += 1
    else:
        details["_finding_quotes"] = "no_expert_quotes"

    return min(score, 25), details


# ── 4. Trust Infrastructure (15 pts) ──────────────────────────────────────

def _score_trust_infra(crawl: CrawlResult) -> tuple[float, dict]:
    """
    Does the site have trust infrastructure?
    About page, contact, privacy policy, editorial standards.
    """
    soup = crawl.soup
    html_lower = crawl.html.lower()
    details = {}
    score = 0.0

    nav_links = set()
    for a in soup.find_all("a", href=True):
        nav_links.add(a["href"].lower())
        nav_links.add(a.get_text(strip=True).lower())
    nav_str = " ".join(nav_links)

    # 4a. About page (4 pts)
    has_about = "about" in nav_str
    details["has_about_page"] = has_about
    if has_about:
        score += 4
    else:
        details["_finding_about"] = "no_about_page"

    # 4b. Contact page (3 pts)
    has_contact = "contact" in nav_str
    details["has_contact_page"] = has_contact
    if has_contact:
        score += 3

    # 4c. Privacy policy (3 pts)
    has_privacy = "privacy" in nav_str
    details["has_privacy_policy"] = has_privacy
    if has_privacy:
        score += 3

    # 4d. Editorial standards / disclosure (3 pts)
    has_editorial = any(x in html_lower for x in [
        "editorial policy", "editorial standards", "fact-check",
        "reviewed by", "medically reviewed", "disclosure",
        "corrections policy", "advertising policy",
    ])
    details["has_editorial_standards"] = has_editorial
    if has_editorial:
        score += 3

    # 4e. Terms of service (2 pts)
    has_terms = any(x in nav_str for x in ["terms", "tos", "legal"])
    details["has_terms"] = has_terms
    if has_terms:
        score += 2

    return min(score, 15), details


# ── Main Scorer ───────────────────────────────────────────────────────────

def score_eeat(crawl: CrawlResult, skip_gemini: bool = False) -> tuple[float, dict]:
    """
    E-E-A-T Score v3 — Believability Score.

    Score = (Identity × 0.25) + (Evidence × 0.35) +
            (Experience × 0.25) + (Trust Infra × 0.15)

    Each sub-score normalized to 0-100, then weighted.
    """
    if not crawl.ok:
        return 0.0, {"error": crawl.error}

    details = {"checks": {}, "findings": []}

    # Score each dimension
    identity_raw, identity_details = _score_identity(crawl)       # max 25
    evidence_raw, evidence_details = _score_evidence(crawl)       # max 35
    experience_raw, experience_details = _score_experience(crawl)  # max 25
    trust_raw, trust_details = _score_trust_infra(crawl)          # max 15

    # Normalize to 0-100
    identity_score = (identity_raw / 25) * 100
    evidence_score = (evidence_raw / 35) * 100
    experience_score = (experience_raw / 25) * 100
    trust_score = (trust_raw / 15) * 100

    # Weighted total
    total = (
        identity_score * 0.25 +
        evidence_score * 0.35 +
        experience_score * 0.25 +
        trust_score * 0.15
    )

    # Store details
    details["checks"]["identity"] = identity_details
    details["checks"]["evidence"] = evidence_details
    details["checks"]["experience"] = experience_details
    details["checks"]["trust_infrastructure"] = trust_details

    details["checks"]["identity_score"] = round(identity_score, 1)
    details["checks"]["evidence_score"] = round(evidence_score, 1)
    details["checks"]["experience_score"] = round(experience_score, 1)
    details["checks"]["trust_score"] = round(trust_score, 1)

    # Collect findings
    for sub in [identity_details, evidence_details, experience_details, trust_details]:
        for key, val in sub.items():
            if key.startswith("_finding"):
                details["findings"].append(val)

    total = safe_score(total)
    details["score"] = total

    return total, details
