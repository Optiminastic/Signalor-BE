"""Brand Visibility orchestrator — runs Google, Reddit, Web Mentions checks."""

import logging
from concurrent.futures import ThreadPoolExecutor

from apps.visibility.pipeline.google_check import check_google
from apps.visibility.pipeline.reddit_check import check_reddit
from apps.visibility.pipeline.web_mentions_check import check_web_mentions

from .ai_brand_perception import run_ai_brand_perception
from .brand_naming import visibility_brand_label
from .social_presence import run_social_presence

logger = logging.getLogger("apps")

# Weights: Google 44%, Reddit 22%, Web Mentions 34%
GOOGLE_WEIGHT = 0.44
REDDIT_WEIGHT = 0.22
WEB_MENTIONS_WEIGHT = 0.34


def extract_brand_name(url: str) -> str:
    """Derive display label from the URL registrable hostname (no user override)."""
    return visibility_brand_label(url, "")


def run_brand_visibility(brand_name: str, brand_url: str) -> dict:
    """Run all platform checks in parallel and return results dict."""
    stored_label = (brand_name or "").strip()
    effective = visibility_brand_label(brand_url, stored_label)
    brand_name = effective

    google_score, google_details = 0.0, {}
    reddit_score, reddit_details = 0.0, {}
    web_mentions_score, web_mentions_details = 0.0, {}

    with ThreadPoolExecutor(max_workers=3) as executor:
        google_future = executor.submit(check_google, brand_name, brand_url)
        reddit_future = executor.submit(check_reddit, brand_name)
        web_mentions_future = executor.submit(check_web_mentions, brand_name, brand_url)

        try:
            google_score, google_details = google_future.result()
        except Exception as exc:
            logger.warning("Brand visibility Google check failed: %s", exc)
            google_details = {"error": str(exc)}

        try:
            reddit_score, reddit_details = reddit_future.result()
        except Exception as exc:
            logger.warning("Brand visibility Reddit check failed: %s", exc)
            reddit_details = {"error": str(exc)}

        try:
            web_mentions_score, web_mentions_details = web_mentions_future.result()
        except Exception as exc:
            logger.warning("Brand visibility Web Mentions check failed: %s", exc)
            web_mentions_details = {"error": str(exc)}

    overall_score = (
        google_score * GOOGLE_WEIGHT
        + reddit_score * REDDIT_WEIGHT
        + web_mentions_score * WEB_MENTIONS_WEIGHT
    )

    try:
        social_presence_details = run_social_presence(
            brand_name, brand_url, web_mentions_details if isinstance(web_mentions_details, dict) else {}
        )
    except Exception as exc:
        logger.warning("Social presence check failed: %s", exc)
        social_presence_details = {"error": str(exc)}

    g_d = google_details if isinstance(google_details, dict) else {}
    r_d = reddit_details if isinstance(reddit_details, dict) else {}
    w_d = web_mentions_details if isinstance(web_mentions_details, dict) else {}
    try:
        ai_brand_facts = run_ai_brand_perception(
            brand_name,
            brand_url,
            g_d,
            r_d,
            w_d,
            stored_brand_name=stored_label,
        )
    except Exception as exc:
        logger.warning("AI brand perception failed: %s", exc)
        ai_brand_facts = {"facts": [], "summary": "", "caveat": str(exc)[:200], "error": str(exc)}

    return {
        "google_score": google_score,
        "google_details": google_details,
        "reddit_score": reddit_score,
        "reddit_details": reddit_details,
        "web_mentions_score": web_mentions_score,
        "web_mentions_details": web_mentions_details,
        "social_presence_details": social_presence_details,
        "ai_brand_facts": ai_brand_facts,
        "overall_score": round(overall_score, 1),
    }
