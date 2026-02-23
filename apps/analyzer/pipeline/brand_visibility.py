"""Brand Visibility orchestrator — runs Google, Reddit, Medium, Web Mentions checks."""

import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from apps.visibility.pipeline.google_check import check_google
from apps.visibility.pipeline.reddit_check import check_reddit
from apps.visibility.pipeline.medium_check import check_medium
from apps.visibility.pipeline.web_mentions_check import check_web_mentions

logger = logging.getLogger("apps")

# Weights: Google 40%, Reddit 20%, Medium 10%, Web Mentions 30%
GOOGLE_WEIGHT = 0.40
REDDIT_WEIGHT = 0.20
MEDIUM_WEIGHT = 0.10
WEB_MENTIONS_WEIGHT = 0.30


def extract_brand_name(url: str) -> str:
    """Derive a brand name from the URL domain."""
    try:
        hostname = urlparse(url).hostname or ""
        # Remove www. and TLD
        parts = hostname.replace("www.", "").split(".")
        if parts:
            return parts[0].capitalize()
    except Exception:
        pass
    return ""


def run_brand_visibility(brand_name: str, brand_url: str) -> dict:
    """Run all 4 platform checks in parallel and return results dict."""
    google_score, google_details = 0.0, {}
    reddit_score, reddit_details = 0.0, {}
    medium_score, medium_details = 0.0, {}
    web_mentions_score, web_mentions_details = 0.0, {}

    with ThreadPoolExecutor(max_workers=4) as executor:
        google_future = executor.submit(check_google, brand_name, brand_url)
        reddit_future = executor.submit(check_reddit, brand_name)
        medium_future = executor.submit(check_medium, brand_name)
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
            medium_score, medium_details = medium_future.result()
        except Exception as exc:
            logger.warning("Brand visibility Medium check failed: %s", exc)
            medium_details = {"error": str(exc)}

        try:
            web_mentions_score, web_mentions_details = web_mentions_future.result()
        except Exception as exc:
            logger.warning("Brand visibility Web Mentions check failed: %s", exc)
            web_mentions_details = {"error": str(exc)}

    overall_score = (
        google_score * GOOGLE_WEIGHT
        + reddit_score * REDDIT_WEIGHT
        + medium_score * MEDIUM_WEIGHT
        + web_mentions_score * WEB_MENTIONS_WEIGHT
    )

    return {
        "google_score": google_score,
        "google_details": google_details,
        "reddit_score": reddit_score,
        "reddit_details": reddit_details,
        "medium_score": medium_score,
        "medium_details": medium_details,
        "web_mentions_score": web_mentions_score,
        "web_mentions_details": web_mentions_details,
        "overall_score": round(overall_score, 1),
    }
