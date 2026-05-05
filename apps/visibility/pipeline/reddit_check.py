"""Reddit Mentions check (score 0-100).

Strategy 1: Perplexity Sonar via OpenRouter — real-time web search, no Reddit API required.
Strategy 2: Reddit OAuth2 direct API (if REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET are set).
Strategy 3: LLM estimation fallback.
"""

import json
import logging
import os
import random
import re
import time

import requests

from .sentiment import analyze_sentiment

logger = logging.getLogger("apps")

REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
USER_AGENT = "web:signalor-geo-visibility:1.0 (contact: support@signalor.ai)"

_EMPTY_DETAILS = {
    "posts": [],
    "subreddits": [],
    "total_mentions": 0,
    "total_upvotes": 0,
    "total_comments": 0,
    "sentiment": {},
}


# ── Scoring helper ────────────────────────────────────────────────────────────

def _compute_score(details: dict) -> float:
    mention_count = details["total_mentions"]
    if mention_count == 0:
        details["sub_scores"] = {"mention_volume": 0, "subreddit_diversity": 0, "engagement": 0}
        return 0.0

    unique_subs = len(details.get("subreddits", []))
    engagement = details["total_upvotes"] + details["total_comments"]

    volume_score = min(100, (mention_count / 15) * 100)
    diversity_score = min(100, (unique_subs / 5) * 100)
    engagement_score = min(100, (engagement / 500) * 100)

    base_score = volume_score * 0.35 + diversity_score * 0.25 + engagement_score * 0.40
    modifier = details.get("sentiment", {}).get("modifier", 0)

    details["sub_scores"] = {
        "mention_volume": round(volume_score, 1),
        "subreddit_diversity": round(diversity_score, 1),
        "engagement": round(engagement_score, 1),
    }

    return round(min(100, max(0, base_score + modifier)), 1)


# ── Strategy 1: Perplexity Sonar ──────────────────────────────────────────────

def _check_via_perplexity(brand_name: str) -> tuple[float, dict] | None:
    """
    Use Perplexity Sonar (real-time web search) to find Reddit mentions.
    No Reddit credentials needed — Perplexity searches the live web.
    Returns (score, details) or None on failure.
    """
    try:
        from apps.analyzer.pipeline.llm import ask_llm, is_available
        if not is_available():
            return None

        prompt = (
            f"Search Reddit for recent posts and discussions mentioning the brand '{brand_name}'. "
            f"Use your web search to find actual Reddit threads.\n\n"
            f"For each Reddit post found, provide:\n"
            f"- title: the post title\n"
            f"- subreddit: subreddit name (without r/)\n"
            f"- upvotes: upvote count (integer, 0 if unknown)\n"
            f"- comments: comment count (integer, 0 if unknown)\n"
            f"- url: full Reddit post URL\n"
            f"- sentiment: 'positive', 'negative', or 'neutral'\n\n"
            f"Reply with ONLY valid JSON, no markdown:\n"
            f'{{\n'
            f'  "posts": [\n'
            f'    {{"title": "...", "subreddit": "...", "upvotes": 0, "comments": 0, '
            f'"url": "https://reddit.com/r/...", "sentiment": "neutral"}}\n'
            f'  ],\n'
            f'  "reasoning": "brief summary of brand Reddit presence"\n'
            f'}}\n\n'
            f"Include up to 15 posts. If none found, return an empty posts array."
        )

        response = ask_llm(
            prompt,
            preferred_provider="perplexity",
            max_tokens=2048,
            purpose="Reddit Mentions (Perplexity Sonar)",
        )
        if not response:
            return None

        match = re.search(r"\{.*\}", response, re.DOTALL)
        if not match:
            return None

        data = json.loads(match.group())
        posts_raw = data.get("posts", [])

        details = dict(_EMPTY_DETAILS)
        details["posts"] = []
        details["method"] = "perplexity_sonar"

        subreddits_set: set[str] = set()
        total_upvotes = 0
        total_comments = 0
        sentiments: dict[str, int] = {"positive": 0, "negative": 0, "neutral": 0}

        for p in posts_raw[:15]:
            title = str(p.get("title", ""))[:200].strip()
            subreddit = str(p.get("subreddit", "")).strip().lstrip("r/").strip()
            upvotes = max(0, int(p.get("upvotes", 0)))
            comments = max(0, int(p.get("comments", 0)))
            url = str(p.get("url", ""))
            sentiment_val = str(p.get("sentiment", "neutral")).lower()
            if sentiment_val not in sentiments:
                sentiment_val = "neutral"

            if not title or not subreddit:
                continue

            if not url.startswith("http"):
                url = f"https://reddit.com/r/{subreddit}/"

            subreddits_set.add(subreddit)
            total_upvotes += upvotes
            total_comments += comments
            sentiments[sentiment_val] += 1

            details["posts"].append({
                "title": title,
                "subreddit": subreddit,
                "upvotes": upvotes,
                "comments": comments,
                "url": url[:300],
            })

        details["subreddits"] = sorted(subreddits_set)
        details["total_mentions"] = len(details["posts"])
        details["total_upvotes"] = total_upvotes
        details["total_comments"] = total_comments

        total_s = max(1, sum(sentiments.values()))
        modifier = round((sentiments["positive"] / total_s - sentiments["negative"] / total_s) * 15, 1)
        details["sentiment"] = {**sentiments, "modifier": modifier}

        return _compute_score(details), details

    except Exception as exc:
        logger.warning("Perplexity Reddit check failed: %s", exc)
        return None


# ── Strategy 2: Reddit direct API ────────────────────────────────────────────

def _get_oauth_token() -> str | None:
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        return None
    try:
        resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception as exc:
        logger.warning("Reddit OAuth token fetch failed: %s", exc)
        return None


def _check_via_direct_api(brand_name: str) -> tuple[float, dict] | None:
    """Search Reddit's API (OAuth preferred, unauthenticated fallback). Returns None on failure."""
    token = _get_oauth_token()
    params = {"q": f'"{brand_name}"', "limit": 50, "sort": "relevance", "t": "all", "type": "link"}

    if token:
        url = "https://oauth.reddit.com/search"
        headers = {"User-Agent": USER_AGENT, "Authorization": f"bearer {token}"}
    else:
        url = "https://www.reddit.com/search.json"
        headers = {"User-Agent": USER_AGENT}

    data = None
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            if resp.status_code in (429, 503, 502):
                wait = (2 ** attempt) + random.uniform(0.5, 1.5)
                logger.warning("Reddit returned %s on attempt %d/3, retrying in %.1fs", resp.status_code, attempt + 1, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.exceptions.RequestException as exc:
            logger.warning("Reddit API attempt %d failed: %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep((2 ** attempt) + random.uniform(0.5, 1.5))

    if not data:
        return None

    details = dict(_EMPTY_DETAILS)
    details["method"] = "reddit_api"
    brand_lower = brand_name.lower().strip()
    subreddits_set: set[str] = set()
    post_titles: list[str] = []
    total_upvotes = 0
    total_comments = 0

    for post in data.get("data", {}).get("children", []):
        p = post.get("data", {})
        title = p.get("title", "")
        selftext = p.get("selftext", "")
        if brand_lower not in f"{title} {selftext}".lower():
            continue

        subreddit = p.get("subreddit", "")
        ups = p.get("ups", 0)
        num_comments = p.get("num_comments", 0)

        subreddits_set.add(subreddit)
        post_titles.append(title)
        total_upvotes += ups
        total_comments += num_comments

        details["posts"].append({
            "title": title[:200],
            "subreddit": subreddit,
            "upvotes": ups,
            "comments": num_comments,
            "url": f"https://reddit.com{p.get('permalink', '')}",
        })

    details["subreddits"] = sorted(subreddits_set)
    details["total_mentions"] = len(details["posts"])
    details["total_upvotes"] = total_upvotes
    details["total_comments"] = total_comments
    details["sentiment"] = (
        analyze_sentiment(post_titles)
        if post_titles
        else {"positive": 0, "negative": 0, "neutral": 0, "modifier": 0}
    )

    return _compute_score(details), details


# ── Strategy 3: LLM estimation ───────────────────────────────────────────────

def _llm_estimation(brand_name: str) -> tuple[float, dict]:
    """Last resort: ask an LLM to estimate Reddit presence from its training data."""
    details = dict(_EMPTY_DETAILS)
    details["method"] = "llm_estimation"

    try:
        from apps.analyzer.pipeline.llm import ask_llm, is_available
        if not is_available():
            return 10.0, {**details, "error": "Reddit data temporarily unavailable."}

        prompt = (
            f"Based on your knowledge, estimate the Reddit presence of the brand '{brand_name}'.\n\n"
            f"Reply with ONLY valid JSON:\n"
            f'{{\n'
            f'  "estimated_mentions": 0,\n'
            f'  "estimated_upvotes": 0,\n'
            f'  "estimated_comments": 0,\n'
            f'  "subreddits": [],\n'
            f'  "sentiment": {{"positive": 0, "negative": 0, "neutral": 0}}\n'
            f'}}'
        )

        response = ask_llm(prompt, preferred_provider="gemini", max_tokens=512, purpose="Reddit LLM Estimation")
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            d = json.loads(match.group())
            mentions = max(0, int(d.get("estimated_mentions", 0)))
            upvotes = max(0, int(d.get("estimated_upvotes", 0)))
            comments = max(0, int(d.get("estimated_comments", 0)))
            subreddits = [str(s).lstrip("r/").strip() for s in d.get("subreddits", [])[:10] if s]
            sent = d.get("sentiment", {})

            details["total_mentions"] = mentions
            details["total_upvotes"] = upvotes
            details["total_comments"] = comments
            details["subreddits"] = subreddits

            total_s = max(1, sent.get("positive", 0) + sent.get("negative", 0) + sent.get("neutral", 0))
            modifier = round((sent.get("positive", 0) / total_s - sent.get("negative", 0) / total_s) * 15, 1)
            details["sentiment"] = {
                "positive": sent.get("positive", 0),
                "negative": sent.get("negative", 0),
                "neutral": sent.get("neutral", 0),
                "modifier": modifier,
            }

            return _compute_score(details), details

    except Exception as exc:
        logger.warning("LLM Reddit estimation failed: %s", exc)

    return 10.0, {**details, "error": "Reddit data temporarily unavailable."}


# ── Public entry point ────────────────────────────────────────────────────────

def check_reddit(brand_name: str) -> tuple[float, dict]:
    """
    Check Reddit mentions for a brand. Tries three strategies in order:
    1. Perplexity Sonar (real-time web search — no Reddit auth required)
    2. Reddit API (OAuth if credentials set, public JSON otherwise)
    3. LLM estimation (training-data knowledge)

    Returns (score 0–100, details_dict).
    """
    if not brand_name.lower().strip():
        return 0.0, dict(_EMPTY_DETAILS)

    result = _check_via_perplexity(brand_name)
    if result is not None:
        return result

    result = _check_via_direct_api(brand_name)
    if result is not None:
        return result

    return _llm_estimation(brand_name)
