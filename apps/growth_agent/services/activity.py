"""
The Activity log, by category: articles, guest posts, keywords, social, the
brand's site, outreach emails. A category is worked out from the log row's
channel and action name, in one place, so the filter and the label agree:
a known channel decides first; otherwise the first category (in RULES order)
whose action prefix matches.
"""

from __future__ import annotations

from django.db.models import Q, QuerySet

from ..models import AgentActionLog

PAGE_SIZE = 50
ALL = "all"
OTHER = "other"

# category -> (channels, action-name prefixes)
RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "articles": (("satellite_content", "owned_content"), ("satellite_",)),
    "guest": (("guest_outreach",), ("guest_",)),
    "keywords": ((), ("keyword", "golden_keyword", "rank_")),
    "social": (("owned_social",), ("social_", "buffer_")),
    "site": ((), ("site_", "indexing_", "bing_")),
    "outreach": (("outreach_email",), ("contact", "finder_", "reply_", "opt_out")),
}
CATEGORIES = (*RULES, OTHER)
KNOWN_CHANNELS = tuple(c for channels, _ in RULES.values() for c in channels)


def category_of(channel: str, action: str) -> str:
    for name, (channels, _) in RULES.items():
        if channel in channels:
            return name
    for name, (_, prefixes) in RULES.items():
        if action.startswith(prefixes):
            return name
    return OTHER


def _prefixes(prefixes: tuple[str, ...]) -> Q:
    query = Q(pk__in=[])
    for prefix in prefixes:
        query |= Q(action__startswith=prefix)
    return query


def _filter(name: str) -> Q:
    """Database version of ``category_of`` for one category."""
    unknown_channel = ~Q(channel__in=KNOWN_CHANNELS)
    names = list(RULES)
    if name == OTHER:
        return unknown_channel & ~_prefixes(tuple(p for _, ps in RULES.values() for p in ps))
    channels, prefixes = RULES[name]
    earlier = tuple(p for other in names[: names.index(name)] for p in RULES[other][1])
    return Q(channel__in=channels) | (unknown_channel & _prefixes(prefixes) & ~_prefixes(earlier))


def page(
    logs: QuerySet[AgentActionLog], category: str = ALL, before: int | None = None
) -> tuple[list, int | None]:
    """(rows newest first, the id to pass as ``before`` for the next page, or None at the end)."""
    if category != ALL:
        logs = logs.filter(_filter(category))
    if before:
        logs = logs.filter(pk__lt=before)
    rows = list(logs.order_by("-id")[: PAGE_SIZE + 1])
    more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    return rows, (rows[-1].pk if more and rows else None)
