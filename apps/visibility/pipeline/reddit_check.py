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

    Sub-scores:
      - mention_volume (35%): number of posts mentioning the brand
      - subreddit_diversity (25%): how many unique subreddits
      - engagement (40%): total upvotes + comments
    """
    details = {
        "posts": [],
        "subreddits": [],
        "total_mentions": 0,
        "total_upvotes": 0,
        "total_comments": 0,
        "sentiment": {},
    }

    try:
        resp = requests.get(
            REDDIT_SEARCH_URL,
            params={
                "q": brand_name,
                "limit": 25,
                "sort": "relevance",
                "t": "year",
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
        details["total_mentions"] = len(posts_data)
        details["total_upvotes"] = total_upvotes
        details["total_comments"] = total_comments

        # Sentiment analysis
        sentiment = analyze_sentiment(post_titles)
        details["sentiment"] = sentiment

    except Exception as exc:
        logger.warning("Reddit search failed: %s", exc)
        return 0.0, {**details, "error": str(exc)}

    # Score calculation
    mention_count = details["total_mentions"]
    unique_subs = len(details["subreddits"])
    engagement = details["total_upvotes"] + details["total_comments"]

    # Mention volume (35%): 25+ posts = 100
    volume_score = min(100, (mention_count / 25) * 100)

    # Subreddit diversity (25%): 10+ subs = 100
    diversity_score = min(100, (unique_subs / 10) * 100)

    # Engagement (40%): 500+ total = 100
    engagement_score = min(100, (engagement / 500) * 100)

    base_score = (
        volume_score * 0.35 + diversity_score * 0.25 + engagement_score * 0.40
    )

    # Apply sentiment modifier
    modifier = details["sentiment"].get("modifier", 0)
    score = base_score + modifier

    details["sub_scores"] = {
        "mention_volume": round(volume_score, 1),
        "subreddit_diversity": round(diversity_score, 1),
        "engagement": round(engagement_score, 1),
    }

    return round(min(100, max(0, score)), 1), details
