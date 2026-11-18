"""
Keyword gaps, pure: which competitors to compare with, and which of their
top-10 keywords the brand is missing. No network, no database.

Rows look like DataForSEO's ranked keywords:
{keyword, position, search_volume, etv, url}.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

TOP_RIVALS = 3
TOP_POSITION = 10
MAX_GAPS = 50
MIN_VOLUME = 10
MIN_BRAND_TOKEN = 4


@dataclass(frozen=True)
class Rival:
    name: str
    domain: str


def _norm(keyword: str) -> str:
    return " ".join(keyword.lower().split())


def _compact(text: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", text.lower()))


def _brand_tokens(name: str, domain: str) -> set[str]:
    """The whole brand name and the domain stem, squashed ("growth labs" -> "growthlabs").

    Whole names only: splitting into words would drop real keywords ("growth" for "Growth Labs").
    """
    stem = domain.split(".")[0] if domain else ""
    return {t for t in (_compact(name), _compact(stem)) if len(t) >= MIN_BRAND_TOKEN}


def _branded(keyword: str, tokens: set[str]) -> bool:
    """A navigational search for a brand ("acme login", "acme hq pricing")."""
    squashed = _compact(keyword)
    return any(token in squashed for token in tokens)


def top_rivals(rivals: list[Rival], lost_prompts: list[dict], *, own_domain: str) -> list[Rival]:
    """The competitors AI answers recommend most often instead of the brand, with a domain."""
    wins = Counter(
        _norm(r.get("name", "")) for prompt in lost_prompts or [] for r in prompt.get("winning_rivals") or []
    )
    usable = [r for r in rivals if r.domain and r.domain != own_domain]
    unique = list({r.domain: r for r in usable}.values())
    ranked = sorted(unique, key=lambda r: -wins.get(_norm(r.name), 0))
    return ranked[:TOP_RIVALS]


def gaps(
    brand_rows: list[dict],
    rival_rows: dict[Rival, list[dict]],
    *,
    brand: Rival,
    previous: set[str] = frozenset(),
) -> list[dict]:
    """Keywords rivals rank top 10 for that the brand doesn't rank for, by search volume.

    Branded searches (a rival's or the brand's own name) are left out: nobody wins those.
    ``previous`` holds last week's gap keywords, so new ones can be marked.
    """
    have = {_norm(r.get("keyword", "")) for r in brand_rows}
    excluded = _brand_tokens(brand.name, brand.domain)
    for rival in rival_rows:
        excluded |= _brand_tokens(rival.name, rival.domain)
    found: dict[str, dict] = {}
    for rival, rows in rival_rows.items():
        for row in rows:
            keyword = _norm(row.get("keyword", ""))
            position = int(row.get("position") or 0)
            volume = int(row.get("search_volume") or 0)
            if not keyword or keyword in have or not 0 < position <= TOP_POSITION or volume < MIN_VOLUME:
                continue
            if _branded(keyword, excluded):
                continue
            gap = found.setdefault(keyword, {"keyword": keyword, "search_volume": volume, "rivals": []})
            gap["search_volume"] = max(gap["search_volume"], volume)
            gap["rivals"].append({"name": rival.name, "domain": rival.domain, "position": position})
    rows = sorted(found.values(), key=lambda g: (-g["search_volume"], g["keyword"]))[:MAX_GAPS]
    for row in rows:
        row["rivals"].sort(key=lambda r: r["position"])
        row["new"] = bool(previous) and row["keyword"] not in previous
    return rows


def brand_rows(rows: list[dict]) -> list[dict]:
    """The brand's own keywords, trimmed to what the tab shows, best traffic first."""
    out = [
        {
            "keyword": _norm(r.get("keyword", "")),
            "position": int(r.get("position") or 0),
            "search_volume": int(r.get("search_volume") or 0),
            "url": r.get("url") or "",
        }
        for r in rows
        if r.get("keyword")
    ]
    return out


GOLDEN_MIN_VOLUME = 500
GOLDEN_LIMIT = 3
TOP_TEN = 10
BIG_MOVE = 3
MOVES_LIMIT = 5


def golden(gap_rows: list[dict], *, first_read: bool) -> list[dict]:
    """The best opportunities worth telling the owner about: high-demand gaps, new this week.

    On the first read every gap is new, so the top ones by volume are taken.
    """
    fresh = [g for g in gap_rows if first_read or g.get("new")]
    worthy = [g for g in fresh if int(g.get("search_volume") or 0) >= GOLDEN_MIN_VOLUME]
    return [
        {
            "keyword": g["keyword"],
            "search_volume": g["search_volume"],
            "rival": (g.get("rivals") or [{}])[0].get("domain", ""),
            "position": (g.get("rivals") or [{}])[0].get("position", 0),
        }
        for g in worthy[:GOLDEN_LIMIT]
    ]


def _positions(rows: list[dict]) -> dict[str, int]:
    return {r["keyword"]: int(r.get("position") or 0) for r in rows if r.get("keyword")}


def rank_changes(previous: list[dict], current: list[dict]) -> dict[str, list[dict]]:
    """How the brand's keywords moved since last week (brand rows from two snapshots).

    up / down: moved by BIG_MOVE places or more. entered / left: crossed into or out of the top 10.
    Biggest search demand first, a few of each.
    """
    before, now = _positions(previous), _positions(current)
    volume = {r["keyword"]: int(r.get("search_volume") or 0) for r in current + previous}
    changes: dict[str, list[dict]] = {"up": [], "down": [], "entered": [], "left": []}
    for keyword in set(before) | set(now):
        old, new = before.get(keyword, 0), now.get(keyword, 0)
        row = {"keyword": keyword, "from": old, "to": new, "search_volume": volume.get(keyword, 0)}
        if new and new <= TOP_TEN and not (old and old <= TOP_TEN):
            changes["entered"].append(row)
        elif old and old <= TOP_TEN and not (new and new <= TOP_TEN):
            changes["left"].append(row)
        elif old and new and old - new >= BIG_MOVE:
            changes["up"].append(row)
        elif old and new and new - old >= BIG_MOVE:
            changes["down"].append(row)
    for name in changes:
        changes[name] = sorted(changes[name], key=lambda r: -r["search_volume"])[:MOVES_LIMIT]
    return changes
