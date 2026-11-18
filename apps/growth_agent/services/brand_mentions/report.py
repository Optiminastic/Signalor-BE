"""
The Brand tab: how much the internet talks about the brand vs each competitor,
week by week, with sentiment, and the latest mentions themselves. No network.
"""

from __future__ import annotations

from collections import defaultdict

from ...models import BrandMention, GrowthProgram

WEEKS_SHOWN = 8
LATEST_PER_PLAYER = 10
SNIPPET_CHARS = 220


def _week_counts(program: GrowthProgram, domains: list[str]) -> list[dict]:
    rows = BrandMention.objects.filter(program=program, domain__in=domains).values(
        "week", "domain", "sentiment"
    )
    counts: dict = defaultdict(lambda: defaultdict(lambda: {"mentions": 0, "positive": 0, "negative": 0}))
    for row in rows:
        cell = counts[row["week"]][row["domain"]]
        cell["mentions"] += 1
        if row["sentiment"] in ("positive", "negative"):
            cell[row["sentiment"]] += 1
    weeks = sorted(counts)[-WEEKS_SHOWN:]
    return [
        {
            "week": week.isoformat(),
            "total": sum(c["mentions"] for c in counts[week].values()),
            "domains": {d: dict(counts[week][d]) for d in domains},
        }
        for week in weeks
    ]


def _latest(program: GrowthProgram, domains: list[str]) -> dict[str, list[dict]]:
    out = {}
    for domain in domains:
        rows = BrandMention.objects.filter(program=program, domain=domain).order_by("-week", "-id")[
            :LATEST_PER_PLAYER
        ]
        out[domain] = [
            {
                "title": r.title,
                "url": r.url,
                "source": r.source,
                "channel": r.channel,
                "published": r.published,
                "sentiment": r.sentiment,
                "snippet": r.snippet[:SNIPPET_CHARS],
            }
            for r in rows
        ]
    return out


def section(program: GrowthProgram, domains: list[str]) -> dict:
    return {"weeks": _week_counts(program, domains), "latest": _latest(program, domains)}
