"""
Site watch, pure: read a fetched homepage and robots.txt into a small state,
and compare two states to find what went wrong (or recovered). No network.

An anomaly is a change, not a standing condition: the same problem is reported
once when it appears (or on the first check), and again only after it was fixed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

SEARCH_BOTS = ("Googlebot", "Bingbot")
AI_BOTS = ("GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended")
CLICK_DROP_SHARE = 0.4
CLICK_DROP_MIN_BEFORE = 20
DAYS_COMPARED = 7


class _Head(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.noindex = False
        self.canonical = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta" and values.get("name", "").lower() in ("robots", "googlebot"):
            self.noindex = self.noindex or "noindex" in values.get("content", "").lower()
        elif tag == "link" and "canonical" in values.get("rel", "").lower():
            self.canonical = values.get("href", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.title:
            self.title = " ".join(data.split())[:200]


def read_homepage(html: str, robots_header: str = "") -> dict:
    head = _Head()
    head.feed(html or "")
    noindex = head.noindex or "noindex" in (robots_header or "").lower()
    return {"title": head.title, "noindex": noindex, "canonical": head.canonical}


def _groups(robots_txt: str) -> list[tuple[set[str], list[str]]]:
    """(user agents, disallow rules) per group, lowercased."""
    groups: list[tuple[set[str], list[str]]] = []
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
                groups.append((agents, rules))
                agents, rules = set(), []
            agents.add(value.lower())
        elif field == "disallow" and agents:
            rules.append(value)
        elif field == "allow" and agents:
            rules.append(f"!{value}")
    if agents:
        groups.append((agents, rules))
    return groups


def blocked_bots(robots_txt: str) -> list[str]:
    """Bots told not to crawl the whole site ("Disallow: /"), by their most specific group."""
    groups = _groups(robots_txt)
    blocked = []
    for bot in (*SEARCH_BOTS, *AI_BOTS):
        own = [rules for agents, rules in groups if bot.lower() in agents]
        rules = own[0] if own else next((r for agents, r in groups if "*" in agents), [])
        if "/" in rules and "!/" not in rules:
            blocked.append(bot)
    return blocked


def sitemap_urls(robots_txt: str) -> list[str]:
    return re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_txt or "")


@dataclass(frozen=True)
class Anomaly:
    kind: str
    severity: str  # "danger" | "attention" | "info" | "recovered"
    detail: dict


def _robots(previous: dict | None, state: dict) -> list[Anomaly]:
    before = set((previous or {}).get("blocked", []))
    now = set(state.get("blocked", []))
    found = []
    newly = sorted(now - before)
    if newly:
        severity = "danger" if any(b in SEARCH_BOTS for b in newly) else "attention"
        found.append(Anomaly("robots_blocked", severity, {"bots": newly}))
    freed = sorted(before - now)
    if previous and freed:
        found.append(Anomaly("robots_unblocked", "recovered", {"bots": freed}))
    return found


def _flag(previous: dict | None, state: dict, key: str) -> tuple[bool, bool]:
    """(was, is) for a yes/no problem; a first check counts as "was fine"."""
    return bool((previous or {}).get(key)), bool(state.get(key))


def compare(previous: dict | None, state: dict) -> list[Anomaly]:
    """What changed for the worse (or got fixed) between two daily checks."""
    found: list[Anomaly] = []
    was, now = _flag(previous, state, "down")
    if now and not was:
        found.append(Anomaly("site_down", "danger", {"status": state.get("status"), "url": state.get("url")}))
    if was and not now:
        found.append(Anomaly("site_back_up", "recovered", {"url": state.get("url")}))
    if now:
        return found  # nothing else can be read from a site that is down
    was, now = _flag(previous, state, "noindex")
    if now and not was:
        found.append(Anomaly("noindex_added", "danger", {"url": state.get("url")}))
    if was and not now:
        found.append(Anomaly("noindex_removed", "recovered", {"url": state.get("url")}))
    found += _robots(previous, state)
    was_missing, missing = _flag(previous, state, "sitemap_missing")
    if missing and not was_missing:
        found.append(Anomaly("sitemap_missing", "attention", {}))
    old_title = (previous or {}).get("title", "")
    if previous and old_title and state.get("title") and state["title"] != old_title:
        found.append(Anomaly("title_changed", "info", {"from": old_title, "to": state["title"]}))
    return found


def clicks_drop(daily: list[dict]) -> dict | None:
    """Google clicks over the last 7 days vs the 7 before, from Search Console's daily trend.

    A drop of 40% or more from at least 20 clicks is worth an alert.
    """
    rows = sorted(daily or [], key=lambda d: d.get("date", ""))
    if len(rows) < DAYS_COMPARED * 2:
        return None
    before = sum(int(d.get("clicks") or 0) for d in rows[-DAYS_COMPARED * 2 : -DAYS_COMPARED])
    after = sum(int(d.get("clicks") or 0) for d in rows[-DAYS_COMPARED:])
    if before < CLICK_DROP_MIN_BEFORE or after > before * (1 - CLICK_DROP_SHARE):
        return None
    return {"before": before, "after": after, "share": round(1 - after / before, 2)}
