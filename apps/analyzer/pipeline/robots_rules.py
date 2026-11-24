"""
Which crawlers a robots.txt shuts out of the whole site. Pure: no network.

One parser for the GEO score, the technical checks and the Growth Agent's site
watch. A bot follows its most specific group (a group naming it), else the "*"
group, as crawlers do: "User-agent: * / Disallow: /" followed by
"User-agent: GPTBot / Allow: /" blocks everyone except GPTBot.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# The AI crawlers Signalor checks. GPTBot (OpenAI), PerplexityBot and ClaudeBot
# (Anthropic) are the ones whose answers customers ask about most.
AI_BOTS = ("GPTBot", "PerplexityBot", "ClaudeBot", "Google-Extended", "ChatGPT-User", "anthropic-ai", "CCBot")
SEARCH_BOTS = ("Googlebot", "Bingbot")


def groups(robots_txt: str) -> list[tuple[set[str], list[str]]]:
    """(user agents, rules) per group, lowercased; an "allow" rule is prefixed with "!"."""
    found: list[tuple[set[str], list[str]]] = []
    agents: set[str] = set()
    rules: list[str] = []
    for raw in (robots_txt or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field, value = (part.strip() for part in line.split(":", 1))
        field = field.lower()
        if field == "user-agent":
            if rules:
                found.append((agents, rules))
                agents, rules = set(), []
            agents.add(value.lower())
        elif field == "disallow" and agents:
            rules.append(value)
        elif field == "allow" and agents:
            rules.append(f"!{value}")
    if agents:
        found.append((agents, rules))
    return found


def _site_blocked(rules: list[str]) -> bool:
    return any(r in ("/", "/*") for r in rules) and not any(r in ("!/", "!/*") for r in rules)


def blocked(robots_txt: str, bots: Iterable[str]) -> list[str]:
    """The bots, of ``bots``, told not to crawl the whole site, by their most specific group."""
    parsed = groups(robots_txt)
    out = []
    for bot in bots:
        own = [rules for agents, rules in parsed if bot.lower() in agents]
        rules = own[0] if own else next((r for agents, r in parsed if "*" in agents), [])
        if _site_blocked(rules):
            out.append(bot)
    return out


def sitemaps(robots_txt: str | None) -> list[str]:
    """The sitemap URLs a robots.txt points to ("Sitemap: ..." lines)."""
    return re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_txt or "")
