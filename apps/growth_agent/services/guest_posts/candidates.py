"""
Which sites to pitch a guest article to. Pure: no DB, no network.

Search queries come from the brand's topics in its positioning report. A page
found by search is a candidate only when it reads like a real guest-post
invitation on the brand's topic, and not like a link farm selling placements.
Sites already cited by AI answers (``citation_gaps``) rank first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

MAX_TOPICS = 3
MAX_TOPIC_WORDS = 6
QUERY_TEMPLATES = (
    '"{topic}" "write for us"',
    '"{topic}" "guest post" guidelines',
    '"{topic}" "contribute" article',
)
INVITATION_MARKERS = (
    "write for us",
    "guest post",
    "guest author",
    "guest contributor",
    "contribute",
    "submission guidelines",
    "submit an article",
    "become a contributor",
)
# Phrases that mark a site selling placements rather than inviting editorial articles.
FARM_MARKERS = (
    "sponsored post price",
    "price per post",
    "paid guest post",
    "buy guest post",
    "dofollow link for",
    "we accept all niches",
    "all topics accepted",
    "casino",
    "cbd",
)
_PRICE = re.compile(r"(\$|usd|€|£)\s?\d{2,4}\s*(per|/)\s*(post|article|link)", re.IGNORECASE)
_TAGS = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)
_WORD = re.compile(r"[a-z0-9]+")
STOP_WORDS = frozenset(
    {"the", "a", "an", "and", "or", "for", "of", "to", "in", "on", "with", "best", "how", "what"}
)


@dataclass(frozen=True)
class PageCheck:
    accepts_guest_posts: bool
    on_topic: bool
    sells_links: bool

    @property
    def ok(self) -> bool:
        return self.accepts_guest_posts and self.on_topic and not self.sells_links


def domain_of(url: str) -> str:
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower().split(":")[0]
    return host.removeprefix("www.")


def topics(report: dict) -> list[str]:
    """Short topics to search for: missing topics first, then short lost prompts."""
    pool = list((report.get("context_gaps") or {}).get("missing_topics") or [])
    pool += [item.get("prompt", "") for item in report.get("lost_prompts") or []]
    seen: set[str] = set()
    chosen: list[str] = []
    for topic in (t.strip() for t in pool):
        if topic and len(topic.split()) <= MAX_TOPIC_WORDS and topic.lower() not in seen:
            seen.add(topic.lower())
            chosen.append(topic)
    return chosen[:MAX_TOPICS]


def queries(topic_list: list[str]) -> list[str]:
    return [template.format(topic=topic) for topic in topic_list for template in QUERY_TEMPLATES]


def rival_names(report: dict) -> set[str]:
    names = {
        rival.get("name", "").strip().lower()
        for item in report.get("lost_prompts") or []
        for rival in item.get("winning_rivals") or []
    }
    return {n for n in names if n}


def cited_domains(report: dict) -> list[str]:
    """Sites AI answers cite where the brand is missing, strongest first."""
    gaps = sorted(report.get("citation_gaps") or [], key=lambda g: -(g.get("prompts") or 0))
    return [domain_of(g["domain"]) for g in gaps if g.get("domain")]


def page_text(html: str) -> str:
    return " ".join(_TAGS.sub(" ", html).lower().split())


def _topic_words(topic_list: list[str]) -> set[str]:
    return {w for t in topic_list for w in _WORD.findall(t.lower()) if w not in STOP_WORDS and len(w) > 2}


def check_page(html: str, topic_list: list[str]) -> PageCheck:
    """Is this a real guest-post invitation on the brand's topic?"""
    text = page_text(html)
    words = set(_WORD.findall(text))
    on_topic = bool(_topic_words(topic_list) & words)
    sells = any(marker in text for marker in FARM_MARKERS) or bool(_PRICE.search(text))
    accepts = any(marker in text for marker in INVITATION_MARKERS)
    return PageCheck(accepts_guest_posts=accepts, on_topic=on_topic, sells_links=sells)


def excluded(domain: str, *, blocked_domains: set[str], rivals: set[str]) -> bool:
    """The brand itself, a satellite site, a competitor, or a site already contacted or planned."""
    if not domain or domain in blocked_domains:
        return True
    label = domain.split(".")[0]
    return any(rival.replace(" ", "") in label for rival in rivals if len(rival) > 2)
