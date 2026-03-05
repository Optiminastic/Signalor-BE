import json
import logging
import re
from urllib.parse import urlparse

from .crawler import CrawlResult, check_file_exists
from .utils import safe_score

logger = logging.getLogger("apps")

TRUST_TLDS = {".gov", ".edu", ".ac.uk", ".gov.uk", ".gov.au", ".edu.au", ".ac.jp"}

TRUST_DOMAINS = {
    "wikipedia.org", "bbc.com", "nytimes.com", "reuters.com",
    "nature.com", "pubmed.ncbi.nlm.nih.gov", "scholar.google.com",
    "forbes.com", "hbr.org", "techcrunch.com", "wsj.com",
    "theguardian.com", "washingtonpost.com", "bloomberg.com",
    "sciencedirect.com", "springer.com", "wiley.com",
    "arxiv.org", "ieee.org", "acm.org",
    "who.int", "cdc.gov", "nih.gov", "fda.gov",
    "harvard.edu", "mit.edu", "stanford.edu", "oxford.ac.uk",
    "cambridge.org", "un.org",
}


def _is_trust_link(href: str) -> bool:
    try:
        parsed = urlparse(href)
        domain = parsed.netloc.lower()
        # Check known trust domains
        for trust in TRUST_DOMAINS:
            if domain.endswith(trust):
                return True
        # Check trust TLDs
        for tld in TRUST_TLDS:
            if domain.endswith(tld):
                return True
    except Exception:
        pass
    return False


# ── Section 1: Structural signals (reliable, no LLM needed) ──────────────

def _score_structural_signals(crawl: CrawlResult) -> tuple[float, dict]:
    """Score verifiable structural E-E-A-T signals from HTML. Max 40 pts."""
    soup = crawl.soup
    details = {}
    score = 0.0

    # 1. External citations (10 pts)
    parsed_base = urlparse(crawl.url)
    external_links = []
    for a in soup.find_all("a", href=True):
        try:
            parsed = urlparse(a["href"])
            if parsed.netloc and parsed.netloc != parsed_base.netloc and parsed.scheme in ("http", "https", ""):
                external_links.append(a["href"])
        except Exception:
            continue

    ext_count = len(external_links)
    details["external_citation_count"] = ext_count
    if ext_count >= 5:
        score += 10
    elif ext_count >= 3:
        score += 7
    elif ext_count >= 1:
        score += 3

    # 2. Trust links — links to authoritative sources (10 pts)
    trust_links = [l for l in external_links if _is_trust_link(l)]
    details["trust_link_count"] = len(trust_links)
    if len(trust_links) >= 3:
        score += 10
    elif len(trust_links) >= 1:
        score += 6
    else:
        details["_finding"] = "no_trust_links"

    # 3. Source diversity — different domains (5 pts)
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

    # 4. Date signals — publish + update (5 pts)
    has_date = False
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag:
        has_date = True
    if not has_date:
        has_date = bool(soup.find("meta", property="article:published_time"))
    details["publish_date"] = has_date
    if has_date:
        score += 3

    has_modified = bool(soup.find("meta", property="article:modified_time"))
    details["updated_date"] = has_modified
    if has_modified:
        score += 2

    # 5. Trust pages exist — about, contact, privacy (10 pts)
    base_url = crawl.url
    nav_links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        text = a.get_text(strip=True).lower()
        nav_links.add(href)
        nav_links.add(text)

    nav_links_list = list(nav_links)
    has_about = any("about" in x for x in nav_links_list)
    has_contact = any("contact" in x for x in nav_links_list)
    has_privacy = any("privacy" in x for x in nav_links_list)
    has_terms = any("terms" in x or "tos" in x for x in nav_links_list)

    details["has_about_page"] = has_about
    details["has_contact_page"] = has_contact
    details["has_privacy_policy"] = has_privacy
    details["has_terms"] = has_terms

    trust_page_count = sum([has_about, has_contact, has_privacy, has_terms])
    score += min(10, trust_page_count * 3)  # 3 pts each, max 10

    return score, details


# ── Section 2: Gemini deep E-E-A-T analysis ──────────────────────────────

def _gemini_eeat_analysis(text: str, url: str) -> tuple[dict, bool]:
    """
    Use Gemini to deeply analyze E-E-A-T quality of actual content.
    Returns (analysis_dict, success_bool).
    """
    try:
        from .llm import ask_llm

        prompt = f"""Analyze this webpage content for Google's E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness) signals.

URL: {url}
Content (first 3000 chars):
{text[:3000]}

Score each dimension 0-10 and explain why. Be strict — most pages score 3-6.

Reply ONLY with this JSON format:
{{
  "experience": {{
    "score": 0-10,
    "signals": ["list of specific experience signals found"],
    "missing": ["what's missing"]
  }},
  "expertise": {{
    "score": 0-10,
    "signals": ["list of expertise signals found"],
    "missing": ["what's missing"]
  }},
  "authoritativeness": {{
    "score": 0-10,
    "signals": ["list of authority signals found"],
    "missing": ["what's missing"]
  }},
  "trustworthiness": {{
    "score": 0-10,
    "signals": ["list of trust signals found"],
    "missing": ["what's missing"]
  }},
  "overall_assessment": "one sentence summary"
}}"""

        raw = ask_llm(prompt, preferred_provider="gemini", max_tokens=1024, purpose="E-E-A-T Analysis")

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
            # Validate structure
            for key in ["experience", "expertise", "authoritativeness", "trustworthiness"]:
                if key not in data:
                    data[key] = {"score": 0, "signals": [], "missing": []}
                if not isinstance(data[key].get("score"), (int, float)):
                    data[key]["score"] = 0
                data[key]["score"] = max(0, min(10, data[key]["score"]))
            return data, True

    except Exception as exc:
        logger.warning("Gemini E-E-A-T analysis failed: %s", exc)

    return {}, False


# ── Section 3: Static-only fallback for E-E-A-T content analysis ─────────

def _static_content_eeat(soup, html_lower: str) -> tuple[float, dict]:
    """Fallback when Gemini is unavailable. Scores content-level E-E-A-T from HTML. Max 60 pts."""
    details = {}
    score = 0.0

    # Author attribution (15 pts)
    author_found = False
    author_name = ""
    author_meta = soup.find("meta", attrs={"name": "author"})
    if author_meta and author_meta.get("content"):
        author_found = True
        author_name = author_meta["content"]
    if not author_found:
        for cls in ["author", "byline", "writer", "post-author", "entry-author"]:
            el = soup.find(class_=re.compile(cls, re.I))
            if el and el.get_text(strip=True):
                author_found = True
                author_name = el.get_text(strip=True)
                break
    if not author_found:
        el = soup.find(attrs={"rel": "author"})
        if el:
            author_found = True
            author_name = el.get_text(strip=True) if el.get_text(strip=True) else "linked"
    # Also check JSON-LD for author
    if not author_found:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                schemas = data if isinstance(data, list) else [data]
                for s in schemas:
                    author = s.get("author", {})
                    if isinstance(author, dict) and author.get("name"):
                        author_found = True
                        author_name = author["name"]
                        break
                    elif isinstance(author, list) and author:
                        author_found = True
                        author_name = author[0].get("name", "") if isinstance(author[0], dict) else str(author[0])
                        break
                    # Check @graph
                    for item in s.get("@graph", []):
                        if isinstance(item, dict) and item.get("author"):
                            a = item["author"]
                            if isinstance(a, dict) and a.get("name"):
                                author_found = True
                                author_name = a["name"]
                                break
            except (json.JSONDecodeError, TypeError):
                continue

    details["author_found"] = author_found
    details["author_name"] = author_name
    if author_found:
        score += 15
    else:
        details["_finding_author"] = "no_author"

    # Author bio / credentials (10 pts)
    bio_found = False
    for cls in ["author-bio", "author-description", "bio", "about-author", "author-info", "post-author-bio"]:
        el = soup.find(class_=re.compile(cls, re.I))
        if el and len(el.get_text(strip=True)) > 30:
            bio_found = True
            break
    details["author_bio"] = bio_found
    if bio_found:
        score += 10
    else:
        details["_finding_bio"] = "no_author_bio"

    # First-person experience language (10 pts)
    experience_patterns = [
        r"\bi tested\b", r"\bi tried\b", r"\bi used\b", r"\bin my experience\b",
        r"\bwe found\b", r"\bwe tested\b", r"\bour team\b", r"\bwe built\b",
        r"\bhands-on\b", r"\bcase study\b", r"\breal.world\b",
    ]
    exp_count = sum(1 for p in experience_patterns if re.search(p, html_lower))
    details["experience_signals"] = exp_count
    if exp_count >= 3:
        score += 10
    elif exp_count >= 1:
        score += 5
    else:
        details["_finding_exp"] = "no_first_hand_experience"

    # Expertise depth signals (10 pts)
    depth_patterns = [
        r"\bfor example\b", r"\bspecifically\b", r"\bin practice\b",
        r"\bthe reason\b", r"\bthis means\b", r"\bhow it works\b",
        r"\bstep.by.step\b", r"\bkey takeaway\b", r"\bpro tip\b",
        r"\bimportant(ly)?\b.*because", r"\bcommon mistake\b",
    ]
    depth_count = sum(1 for p in depth_patterns if re.search(p, html_lower))
    details["expertise_depth_signals"] = depth_count
    if depth_count >= 4:
        score += 10
    elif depth_count >= 2:
        score += 6
    elif depth_count >= 1:
        score += 3
    else:
        details["_finding_depth"] = "no_expertise_indicators"

    # Transparency signals (15 pts)
    transparency_score = 0
    # Disclosure / editorial standards
    has_disclosure = any(x in html_lower for x in [
        "disclosure", "editorial policy", "editorial standards",
        "fact-check", "reviewed by", "medically reviewed",
        "affiliate", "sponsored", "advertising policy",
    ])
    details["has_disclosure"] = has_disclosure
    if has_disclosure:
        transparency_score += 5

    # Clear contact/organization info
    has_org_info = any(x in html_lower for x in [
        "about us", "our team", "our mission", "founded in",
        "headquarters", "our story",
    ])
    details["has_org_info"] = has_org_info
    if has_org_info:
        transparency_score += 5

    # Sources mentioned in text (not just linked)
    source_patterns = [
        r"according to", r"source:", r"sources:", r"data from",
        r"published in", r"as reported by", r"study by", r"research from",
    ]
    source_count = sum(1 for p in source_patterns if re.search(p, html_lower))
    details["source_mentions"] = source_count
    if source_count >= 2:
        transparency_score += 5
    elif source_count >= 1:
        transparency_score += 3

    score += transparency_score

    return score, details


# ── Main scorer ───────────────────────────────────────────────────────────

def score_eeat(crawl: CrawlResult, skip_gemini: bool = False) -> tuple[float, dict]:
    """
    E-E-A-T scoring with hybrid approach:
    - Structural signals (40 pts): links, dates, trust pages — always reliable
    - Content E-E-A-T (60 pts): Gemini deep analysis if available, static fallback otherwise
    - skip_gemini: if True, use static-only scoring (for competitor analysis)
    """
    if not crawl.ok:
        return 0.0, {"error": crawl.error}

    details = {"checks": {}, "findings": []}

    # Part 1: Structural signals (max 40 pts)
    structural_score, structural_details = _score_structural_signals(crawl)
    details["checks"]["structural"] = structural_details

    # Collect structural findings
    if not structural_details.get("publish_date"):
        details["findings"].append("no_publish_date")
    if not structural_details.get("updated_date"):
        details["findings"].append("no_updated_date")
    if structural_details.get("external_citation_count", 0) < 3:
        details["findings"].append("few_external_citations")
    if structural_details.get("_finding"):
        details["findings"].append(structural_details["_finding"])
    if structural_details.get("source_diversity", 0) < 3:
        details["findings"].append("low_source_diversity")
    if not structural_details.get("has_about_page"):
        details["findings"].append("no_about_page")

    # Part 2: Content E-E-A-T (max 60 pts)
    if skip_gemini:
        gemini_result, gemini_ok = {}, False
    else:
        gemini_result, gemini_ok = _gemini_eeat_analysis(crawl.text, crawl.url)

    if gemini_ok:
        details["checks"]["scoring_mode"] = "gemini"
        details["checks"]["gemini_analysis"] = gemini_result

        # Convert Gemini 0-10 scores to points (each dimension = 15 pts, total 60)
        exp_score = gemini_result.get("experience", {}).get("score", 0)
        expertise_score = gemini_result.get("expertise", {}).get("score", 0)
        authority_score = gemini_result.get("authoritativeness", {}).get("score", 0)
        trust_score = gemini_result.get("trustworthiness", {}).get("score", 0)

        content_eeat_score = (
            (exp_score / 10) * 15
            + (expertise_score / 10) * 15
            + (authority_score / 10) * 15
            + (trust_score / 10) * 15
        )

        details["checks"]["experience_score"] = exp_score
        details["checks"]["expertise_score"] = expertise_score
        details["checks"]["authoritativeness_score"] = authority_score
        details["checks"]["trustworthiness_score"] = trust_score

        # Generate findings from Gemini analysis
        if exp_score < 4:
            details["findings"].append("no_first_hand_experience")
        if expertise_score < 4:
            details["findings"].append("no_expertise_indicators")
        if authority_score < 4:
            details["findings"].append("low_authority")
        if trust_score < 4:
            details["findings"].append("low_trust_signals")

        # Include Gemini's assessment
        if gemini_result.get("overall_assessment"):
            details["checks"]["assessment"] = gemini_result["overall_assessment"]

    else:
        # Fallback: static content analysis
        details["checks"]["scoring_mode"] = "static_fallback"
        content_eeat_score, static_details = _static_content_eeat(crawl.soup, crawl.html.lower())
        details["checks"]["static_analysis"] = static_details

        # Collect static findings
        for key, val in static_details.items():
            if key.startswith("_finding"):
                details["findings"].append(val)

    total = structural_score + content_eeat_score
    total = safe_score(total)
    details["score"] = total
    details["checks"]["structural_score"] = round(structural_score, 1)
    details["checks"]["content_eeat_score"] = round(content_eeat_score, 1)

    return total, details
