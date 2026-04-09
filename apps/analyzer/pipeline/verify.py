"""
Real verification engine — re-crawls the page and checks if a specific
finding still exists.

Usage:
    from apps.analyzer.pipeline.verify import verify_finding
    result = verify_finding(url, finding_key, pillar)
    # result = {"verified": True/False, "message": "...", "details": {...}}
"""

import logging

from .crawler import crawl_page

logger = logging.getLogger("apps")

# Map each finding_key to the pillar scorer that detects it
FINDING_TO_PILLAR = {
    # Content
    "no_h1": "content",
    "multiple_h1": "content",
    "broken_heading_hierarchy": "content",
    "no_faq_section": "content",
    "no_lists": "content",
    "no_answer_first": "content",
    "few_internal_links": "content",
    "no_citations": "content",
    "no_statistics": "content",
    "no_expert_quotes": "content",
    "weak_authoritative_tone": "content",
    "poor_readability": "content",
    "no_technical_terms": "content",
    "low_vocabulary_diversity": "content",
    "low_word_count": "content",
    "poor_paragraph_structure": "content",
    "keyword_stuffing": "content",
    # Schema
    "no_jsonld": "schema",
    "no_faqpage_schema": "schema",
    "no_article_schema": "schema",
    "no_organization_schema": "schema",
    "invalid_jsonld_structure": "schema",
    "incomplete_article_schema": "schema",
    "incomplete_organization_schema": "schema",
    "incomplete_faqpage_schema": "schema",
    "incomplete_product_schema": "schema",
    "incomplete_blogposting_schema": "schema",
    "incomplete_newsarticle_schema": "schema",
    "incomplete_howto_schema": "schema",
    # E-E-A-T
    "no_author": "eeat",
    "no_author_bio": "eeat",
    "no_publish_date": "eeat",
    "no_updated_date": "eeat",
    "few_external_citations": "eeat",
    "no_trust_links": "eeat",
    "low_source_diversity": "eeat",
    "no_about_page": "eeat",
    "no_first_hand_experience": "eeat",
    "no_expertise_indicators": "eeat",
    "low_authority": "eeat",
    "low_trust_signals": "eeat",
    # Technical
    "no_llms_txt": "technical",
    "ai_bots_blocked": "technical",
    "no_sitemap": "technical",
    "crawl_failed": "technical",
    "meta_noindex": "technical",
    "no_https": "technical",
    "slow_load_time": "technical",
    "no_viewport": "technical",
    "no_canonical": "technical",
    "crawl_blocked_403": "technical",
    "crawl_timeout": "technical",
    # Entity
    "brand_not_in_ai": "entity",
    "no_social_profiles": "entity",
    "no_wikipedia_presence": "entity",
    "no_reddit_presence": "entity",
    "no_medium_presence": "entity",
    # AI Visibility
    "not_in_google_ai": "ai_visibility",
    "no_reddit_ai_presence": "ai_visibility",
    "no_medium_ai_presence": "ai_visibility",
    "weak_brand_site": "ai_visibility",
}

# Findings that can't be verified by re-crawl (require external checks)
SKIP_RECRAWL = {
    "brand_not_in_ai", "no_wikipedia_presence", "no_reddit_presence",
    "no_medium_presence", "not_in_google_ai", "no_reddit_ai_presence",
    "no_medium_ai_presence", "weak_brand_site",
}


def _run_pillar_scorer(pillar: str, crawl):
    """Run a single pillar scorer and return its findings list."""
    if pillar == "content":
        from .content import score_content
        _score, details = score_content(crawl)
    elif pillar == "schema":
        from .schema import score_schema
        _score, details = score_schema(crawl)
    elif pillar == "eeat":
        from .eeat import score_eeat
        _score, details = score_eeat(crawl, skip_gemini=True)
    elif pillar == "technical":
        from .technical import score_technical
        _score, details = score_technical(crawl)
    elif pillar == "entity":
        from .entity import score_entity
        _score, details = score_entity(crawl)
    elif pillar == "ai_visibility":
        from .ai_visibility import score_ai_visibility
        _score, details, _probes = score_ai_visibility(crawl)
    else:
        return []

    return details.get("findings", [])


def verify_finding(url: str, finding_key: str, pillar: str = "") -> dict:
    """
    Re-crawl the page and check if a specific finding still exists.

    Returns:
        {
            "verified": bool,       # True = fix confirmed (finding gone)
            "message": str,         # Human-readable result
            "finding_key": str,
            "still_present": bool,  # True = issue still exists
        }
    """
    if not finding_key:
        return {
            "verified": False,
            "message": "No finding key — cannot verify.",
            "finding_key": "",
            "still_present": False,
        }

    # Can't verify off-page/external findings by re-crawling
    if finding_key in SKIP_RECRAWL:
        return {
            "verified": True,
            "message": "This is an off-page action — marked as done. Re-run full analysis to see updated scores.",
            "finding_key": finding_key,
            "still_present": False,
        }

    target_pillar = pillar or FINDING_TO_PILLAR.get(finding_key, "")
    if not target_pillar:
        return {
            "verified": False,
            "message": f"Unknown finding '{finding_key}' — cannot verify.",
            "finding_key": finding_key,
            "still_present": False,
        }

    # Re-crawl the page
    try:
        crawl = crawl_page(url)
    except Exception as e:
        logger.warning("Verify crawl failed for %s: %s", url, e)
        return {
            "verified": False,
            "message": f"Could not reach the page: {e}",
            "finding_key": finding_key,
            "still_present": True,
        }

    if not crawl.ok:
        return {
            "verified": False,
            "message": f"Page returned an error: {crawl.error or 'unreachable'}",
            "finding_key": finding_key,
            "still_present": True,
        }

    # Run only the relevant pillar scorer
    try:
        current_findings = _run_pillar_scorer(target_pillar, crawl)
    except Exception as e:
        logger.exception("Verify scorer failed for %s/%s", target_pillar, finding_key)
        return {
            "verified": False,
            "message": f"Verification check failed: {e}",
            "finding_key": finding_key,
            "still_present": True,
        }

    # Check if the finding is still present
    if finding_key in current_findings:
        return {
            "verified": False,
            "message": "Issue still detected on the page. Make sure you've published your changes and try again.",
            "finding_key": finding_key,
            "still_present": True,
        }

    return {
        "verified": True,
        "message": "Fix confirmed! The issue is no longer detected on your page.",
        "finding_key": finding_key,
        "still_present": False,
    }
