"""
The month's satellite topic plan. Pure: no DB, no LLM.

signalor.ai owns 5 editorial sites (``settings.SATELLITE_SITES``). Each month
the agent plans up to 10 articles for a client, one every 3 days, on topics
where AI answers leave the client out:

1. prompts competitors win and the client doesn't (``lost_prompts``);
2. then topics AI answers cover without the client (``missing_topics``).

Each topic goes to the site whose format fits it (a "vs" question to the
comparison site, a "best" question to the listicle site, ...), and no site gets
more than ``MAX_PER_SITE`` of a client's articles a month. The chosen site's
address is the task's ``target_url``.
"""

from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import urlparse

from .candidates import TaskSpec, _spec
from .tactics import TACTICS

MAX_TOPICS = 10
MAX_PER_SITE = 3
LOST_PROMPT_REACH = 0.8
TOPIC_REACH = 0.6
DEFAULT_SITE = "research"
# First match wins; patterns are checked against the lower-cased topic.
SITE_RULES: tuple[tuple[str, str], ...] = (
    ("comparison", r"\b(vs\.?|versus|compared?|comparison|alternatives?)\b"),
    ("listicals", r"\b(best|top \d*|top|leading|tools for|list of)\b"),
    ("step_guide", r"\b(how to|how do|steps?|guide|set ?up|tutorial)\b"),
    ("market_trends", r"\b(trends?|future|forecast|market|20\d\d)\b"),
)


def best_site(topic: str) -> str:
    """The satellite site whose format fits the topic."""
    text = topic.lower()
    return next((site for site, pattern in SITE_RULES if re.search(pattern, text)), DEFAULT_SITE)


def _topics(report: dict) -> list[tuple[str, float]]:
    """(topic, reach): lost prompts first, then missing topics, case-insensitively unique."""
    seen: set[str] = set()
    ordered: list[tuple[str, float]] = []
    lost = [(item.get("prompt", ""), LOST_PROMPT_REACH) for item in report.get("lost_prompts") or []]
    missing = [(t, TOPIC_REACH) for t in (report.get("context_gaps") or {}).get("missing_topics") or []]
    for topic, reach in lost + missing:
        topic = (topic or "").strip()
        if topic and topic.lower() not in seen:
            seen.add(topic.lower())
            ordered.append((topic, reach))
    return ordered[:MAX_TOPICS]


def _place(topic: str, load: dict[str, int], sites: dict[str, str]) -> str:
    """The best-fit site, or the least-used one if that site already has its share."""
    site = best_site(topic)
    if site not in sites or load.get(site, 0) >= MAX_PER_SITE:
        site = min(sites, key=lambda s: (load.get(s, 0), s))
    load[site] = load.get(site, 0) + 1
    return site


def satellite_candidates(report: dict, sites: dict[str, str]) -> list[TaskSpec]:
    """Up to 10 article specs. ``sites`` maps site key -> site address (``SATELLITE_SITES``)."""
    sites = {key: url for key, url in sites.items() if url}
    if not sites:
        return []
    tactic = TACTICS["satellite_article"]
    load: dict[str, int] = {}
    specs = []
    for topic, reach in _topics(report):
        site_url = sites[_place(topic, load, sites)]
        spec = _spec(tactic, target=topic, reach=reach, target_url=site_url, prompts=(topic,))
        specs.append(replace(spec, title=f"Article on {urlparse(site_url).netloc or site_url}: {topic}"))
    return specs
