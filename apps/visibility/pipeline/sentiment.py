"""Shared sentiment analysis using keyword matching."""

POSITIVE_KEYWORDS = [
    "great", "excellent", "amazing", "love", "best", "awesome", "fantastic",
    "recommend", "helpful", "useful", "reliable", "innovative", "outstanding",
    "impressive", "quality", "perfect", "wonderful", "superior", "top-notch",
    "satisfied", "trust", "professional", "powerful", "efficient",
]

NEGATIVE_KEYWORDS = [
    "terrible", "worst", "hate", "awful", "scam", "fraud", "avoid",
    "horrible", "disappointing", "broken", "useless", "overpriced",
    "unreliable", "poor", "bad", "waste", "slow", "buggy", "spam",
    "misleading", "complaint", "frustrated", "angry", "ripoff",
]


def analyze_sentiment(texts: list[str]) -> dict:
    """
    Analyze sentiment of a list of texts using keyword matching.
    Returns: {"positive": int, "negative": int, "neutral": int, "modifier": float}
    modifier ranges from -20 to +20
    """
    positive = 0
    negative = 0
    neutral = 0

    for text in texts:
        lower = text.lower()
        pos_count = sum(1 for kw in POSITIVE_KEYWORDS if kw in lower)
        neg_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw in lower)

        if pos_count > neg_count:
            positive += 1
        elif neg_count > pos_count:
            negative += 1
        else:
            neutral += 1

    total = len(texts) or 1
    # modifier: +20 if all positive, -20 if all negative
    modifier = ((positive - negative) / total) * 20

    return {
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "modifier": round(modifier, 1),
    }
