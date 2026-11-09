"""
Pure positioning metrics: share of voice, citation gap, lost prompts, entity gaps.

No database, no network. Everything takes ``PositioningInputs`` and returns
JSON-ready dicts, so the report can be stored and rendered as-is.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from apps.analyzer.pipeline.rank_tracker import detect_brand_mentions

from .inputs import EngineAnswer, PositioningInputs

MAX_GAP_DOMAINS = 15
MAX_LOST_PROMPTS = 10
MAX_EXAMPLE_URLS = 2
PERCENT = 100

REVIEW_DOMAINS = (
    "g2.com",
    "capterra.com",
    "trustpilot.com",
    "getapp.com",
    "softwareadvice.com",
    "trustradius.com",
)
COMMUNITY_DOMAINS = (
    "reddit.com",
    "quora.com",
    "stackexchange.com",
    "stackoverflow.com",
    "news.ycombinator.com",
)
VIDEO_DOMAINS = ("youtube.com", "youtu.be", "vimeo.com")
REFERENCE_DOMAINS = ("wikipedia.org", "wikidata.org", "crunchbase.com")
LISTICLE_HINTS = ("best", "top-", "top ", " vs", "-vs-", "alternatives", "compared", "comparison", "review")


@dataclass(frozen=True)
class _MatchedAnswer:
    answer: EngineAnswer
    brand: bool
    rivals: tuple[str, ...]


def _percent(part: int, whole: int) -> float:
    return round(PERCENT * part / whole, 1) if whole else 0.0


def _ends_with_any(domain: str, suffixes: tuple[str, ...]) -> bool:
    return any(domain == s or domain.endswith(f".{s}") for s in suffixes)


def classify_source(domain: str, sample_title: str = "", sample_url: str = "") -> str:
    """Rough source type, used to route a gap to the right off-page channel."""
    if _ends_with_any(domain, REVIEW_DOMAINS):
        return "review_site"
    if _ends_with_any(domain, COMMUNITY_DOMAINS):
        return "community"
    if _ends_with_any(domain, VIDEO_DOMAINS):
        return "video"
    if _ends_with_any(domain, REFERENCE_DOMAINS):
        return "reference"
    text = f"{sample_title} {sample_url}".lower()
    if any(hint in text for hint in LISTICLE_HINTS):
        return "listicle"
    return "publisher"


def match_answers(inputs: PositioningInputs) -> list[_MatchedAnswer]:
    rival_names = [r.name for r in inputs.rivals]
    matched = []
    for answer in inputs.answers:
        brand_hit, rivals_found = detect_brand_mentions(
            answer.response_text, "", list(inputs.brand_names), rival_names, brand_domain=inputs.brand_domain
        )
        matched.append(_MatchedAnswer(answer, answer.brand_mentioned or brand_hit, tuple(rivals_found)))
    return matched


def share_of_voice(inputs: PositioningInputs, matched: list[_MatchedAnswer]) -> dict:
    """Percent of AI answers mentioning the brand vs each competitor, overall and per engine."""
    by_engine: dict[str, list[_MatchedAnswer]] = defaultdict(list)
    for item in matched:
        by_engine[item.answer.engine].append(item)

    def rates(items: list[_MatchedAnswer]) -> dict:
        total = len(items)
        return {
            "answers": total,
            "brand": _percent(sum(1 for i in items if i.brand), total),
            "rivals": {
                r.name: _percent(sum(1 for i in items if r.name in i.rivals), total) for r in inputs.rivals
            },
        }

    return {
        "overall": rates(matched),
        "engines": {engine: rates(items) for engine, items in sorted(by_engine.items())},
    }


def _is_owned(domain: str, inputs: PositioningInputs) -> bool:
    owned = [inputs.brand_domain, *(r.domain for r in inputs.rivals)]
    return any(d and (domain == d or domain.endswith(f".{d}")) for d in owned)


def citation_gaps(inputs: PositioningInputs, matched: list[_MatchedAnswer]) -> list[dict]:
    """Third-party domains AI cites in answers that leave the brand out, ranked by reach."""
    stats: dict[str, dict] = {}
    for item in matched:
        if item.brand:
            continue
        for citation in item.answer.citations:
            domain = citation.domain
            if not domain or _is_owned(domain, inputs):
                continue
            entry = stats.setdefault(
                domain, {"citations": 0, "prompts": set(), "engines": set(), "rivals": set(), "examples": []}
            )
            entry["citations"] += 1
            entry["prompts"].add(item.answer.prompt_id)
            entry["engines"].add(item.answer.engine)
            entry["rivals"].update(item.rivals)
            if len(entry["examples"]) < MAX_EXAMPLE_URLS and citation.url not in entry["examples"]:
                entry["examples"].append(citation.url)
                entry.setdefault("title", citation.title)

    ranked = sorted(
        stats.items(),
        key=lambda kv: (len(kv[1]["prompts"]), len(kv[1]["engines"]), kv[1]["citations"]),
        reverse=True,
    )
    return [
        {
            "domain": domain,
            "source_type": classify_source(domain, entry.get("title", ""), entry["examples"][0]),
            "citations": entry["citations"],
            "prompts": len(entry["prompts"]),
            "engines": sorted(entry["engines"]),
            "rivals_present": sorted(entry["rivals"]),
            "example_urls": entry["examples"],
        }
        for domain, entry in ranked[:MAX_GAP_DOMAINS]
    ]


def lost_prompts(matched: list[_MatchedAnswer]) -> list[dict]:
    """Prompts where no engine mentions the brand but at least one competitor appears."""
    grouped: dict[int, list[_MatchedAnswer]] = defaultdict(list)
    for item in matched:
        grouped[item.answer.prompt_id].append(item)

    lost = []
    for items in grouped.values():
        if any(i.brand for i in items):
            continue
        rival_engines: dict[str, set[str]] = defaultdict(set)
        for i in items:
            for name in i.rivals:
                rival_engines[name].add(i.answer.engine)
        if rival_engines:
            winners = sorted(rival_engines, key=lambda n: len(rival_engines[n]), reverse=True)
            lost.append(
                {
                    "prompt": items[0].answer.prompt_text,
                    "winning_rivals": [{"name": n, "engines": sorted(rival_engines[n])} for n in winners],
                }
            )
    lost.sort(key=lambda p: sum(len(r["engines"]) for r in p["winning_rivals"]), reverse=True)
    return lost[:MAX_LOST_PROMPTS]


def entity_gaps(inputs: PositioningInputs) -> list[dict]:
    """Missing entity signals AI engines use to recognise a brand."""
    checks = inputs.entity_checks
    community = checks.get("community_links") or {}
    candidates = [
        ("wikipedia", not checks.get("wikipedia_presence", False), "No Wikipedia page"),
        ("knowledge_panel", not checks.get("knowledge_panel", False), "No Google knowledge panel"),
        ("reddit", community.get("reddit") is False, "No Reddit presence"),
        ("social", int(checks.get("social_count") or 0) < 3, "Fewer than 3 linked social profiles"),
    ]
    return [{"signal": key, "label": label} for key, missing, label in candidates if missing and checks]


def headline(sov: dict, gaps: list[dict], lost: list[dict], inputs: PositioningInputs) -> dict:
    overall = sov["overall"]
    rival_rates = overall["rivals"]
    top_rival = max(rival_rates, key=rival_rates.get) if rival_rates else None
    return {
        "brand_share_of_voice": overall["brand"],
        "answers_analysed": overall["answers"],
        "prompts_analysed": len({a.prompt_id for a in inputs.answers}),
        "top_rival": top_rival,
        "top_rival_share_of_voice": rival_rates.get(top_rival, 0.0) if top_rival else 0.0,
        "gap_domains": len(gaps),
        "lost_prompts": len(lost),
    }


def compute_metrics(inputs: PositioningInputs) -> dict:
    matched = match_answers(inputs)
    sov = share_of_voice(inputs, matched)
    gaps = citation_gaps(inputs, matched)
    lost = lost_prompts(matched)
    return {
        "headline": headline(sov, gaps, lost, inputs),
        "share_of_voice": sov,
        "citation_gaps": gaps,
        "lost_prompts": lost,
        "entity_gaps": entity_gaps(inputs),
    }
