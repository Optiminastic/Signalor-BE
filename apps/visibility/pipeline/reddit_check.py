"""Reddit Mentions check (score 0-100)."""

import logging

import requests

from .sentiment import analyze_sentiment

logger = logging.getLogger("apps")

REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"
USER_AGENT = "OptiminsticVisibilityBot/1.0"


def check_reddit(brand_name: str) -> tuple[float, dict]:
    """
    Check Reddit mentions for a brand.
    Returns (score, details_dict).

    Only counts posts that ACTUALLY mention the brand name in the title or selftext.
    """
    details = {
        "posts": [],
        "subreddits": [],
        "total_mentions": 0,
        "total_upvotes": 0,
        "total_comments": 0,
        "sentiment": {},
    }

    brand_lower = brand_name.lower().strip()
    if not brand_lower:
        return 0.0, details

    try:
        # Use quoted search for exact brand match
        resp = requests.get(
            REDDIT_SEARCH_URL,
            params={
                "q": f'"{brand_name}"',
                "limit": 50,
                "sort": "relevance",
                "t": "all",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        posts_data = data.get("data", {}).get("children", [])
        subreddits_set = set()
        post_titles = []
        total_upvotes = 0
        total_comments = 0

        for post in posts_data:
            p = post.get("data", {})
            title = p.get("title", "")
            selftext = p.get("selftext", "")

            # Only count posts that actually mention the brand
            combined = f"{title} {selftext}".lower()
            if brand_lower not in combined:
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

        # Sentiment — only on posts that actually mention the brand
        if post_titles:
            sentiment = analyze_sentiment(post_titles)
        else:
            sentiment = {"positive": 0, "negative": 0, "neutral": 0, "modifier": 0}
        details["sentiment"] = sentiment

    except Exception as exc:
        logger.warning("Reddit search failed: %s", exc)
        return 0.0, {**details, "error": str(exc)}

    mention_count = details["total_mentions"]
    unique_subs = len(details["subreddits"])
    engagement = details["total_upvotes"] + details["total_comments"]

    # No real mentions → score 0
    if mention_count == 0:
        details["sub_scores"] = {"mention_volume": 0, "subreddit_diversity": 0, "engagement": 0}
        return 0.0, details

    # Mention volume (35%): 15+ genuine posts = 100
    volume_score = min(100, (mention_count / 15) * 100)

    # Subreddit diversity (25%): 5+ subs = 100
    diversity_score = min(100, (unique_subs / 5) * 100)

    # Engagement (40%): 500+ total = 100
    engagement_score = min(100, (engagement / 500) * 100)

    base_score = (
        volume_score * 0.35 + diversity_score * 0.25 + engagement_score * 0.40
    )

    modifier = details["sentiment"].get("modifier", 0)
    score = base_score + modifier

    details["sub_scores"] = {
        "mention_volume": round(volume_score, 1),
        "subreddit_diversity": round(diversity_score, 1),
        "engagement": round(engagement_score, 1),
    }

    return round(min(100, max(0, score)), 1), details
