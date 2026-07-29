"""Entity disambiguation: does the engine know who you are at all?

Before an engine can decide whether to cite a brand, it has to resolve the name
to an entity. When it cannot, it does one of three things, and all three are
visible in the answer text already stored on every ``PromptResult``:

* says it does not recognise the term,
* proposes a spelling correction ("did you mean signaler?"),
* or confidently describes a *different* thing that shares the name.

None of that shows up as a citation metric. The prompt simply reads as "not
mentioned", indistinguishable from a brand the engine knows and chose to omit.
Those are completely different problems: the second needs better content, the
first needs the entity to exist in the engine's world at all, and no amount of
on-page work fixes it.

**Detection is deterministic, not modelled.** Every signal here is a phrase match
against the stored answer plus the alternative name the engine itself proposed.
Asking a model "is this engine confused?" would introduce exactly the guesswork
this module exists to replace, and it would cost a call per response.

Confusion is counted per *response*, so an engine that gets the brand right in
nine prompts and wrong in one reads as 10% confused rather than "confused".
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field

logger = logging.getLogger("apps")

# Above this share of responses, name resolution is the bottleneck and content
# work is premature. Deliberately not 0: a single odd answer is noise.
CONFUSION_THRESHOLD = 0.30

# Phrases that mean "I could not resolve this name". Matched case-insensitively
# against the answer text.
_UNRECOGNISED = (
    r"not a widely (?:recognized|recognised|known)",
    r"(?:i'?m|i am) not familiar with",
    r"does(?:n'?t| not) (?:appear to )?refer to",
    r"(?:isn'?t|is not) a (?:widely )?(?:recognized|recognised|standard|known) (?:term|brand|product|company)",
    r"no (?:widely )?(?:recognized|recognised|known) (?:term|brand|product|company)",
    r"could not find (?:any )?information",
    r"unable to find (?:any )?information",
    r"there is no (?:widely )?(?:known|recognized|recognised)",
)

# Patterns where the engine proposes a different name. Group 1 is the alternative
# it suggested, which is the most useful part: it names the entity you are losing
# your identity to.
_CORRECTIONS = (
    r"did you mean[:\s]+[\"'“]?([A-Za-z][A-Za-z0-9 .&'-]{1,38})",
    r"(?:perhaps|maybe|possibly) you (?:meant|mean)[:\s]+[\"'“]?([A-Za-z][A-Za-z0-9 .&'-]{1,38})",
    r"(?:a )?(?:possible )?(?:typo|misspelling|mis-spelling)(?: of| for)[:\s]+[\"'“]?([A-Za-z][A-Za-z0-9 .&'-]{1,38})",
    r"you may be (?:thinking of|referring to)[:\s]+[\"'“]?([A-Za-z][A-Za-z0-9 .&'-]{1,38})",
    r"(?:confused|confusing) (?:it )?with[:\s]+[\"'“]?([A-Za-z][A-Za-z0-9 .&'-]{1,38})",
)

_UNRECOGNISED_RE = [re.compile(p, re.I) for p in _UNRECOGNISED]
_CORRECTIONS_RE = [re.compile(p, re.I) for p in _CORRECTIONS]

# Trailing words a greedy capture picks up but that are not part of a name.
_TRAILING = re.compile(r"\b(?:or|and|which|that|the|a|an|is|are|was|in|for|to|by)\b.*$", re.I)


@dataclass
class ConfusionSignal:
    engine: str
    prompt: str
    kind: str  # "unrecognised" | "correction"
    suggested: str = ""
    excerpt: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DisambiguationReport:
    brand: str
    responses: int = 0
    confused: int = 0
    confusion_rate: float = 0.0
    by_engine: dict = field(default_factory=dict)
    top_alternatives: list[dict] = field(default_factory=list)
    signals: list[ConfusionSignal] = field(default_factory=list)
    known_collision: dict | None = None
    is_blocking: bool = False

    def as_dict(self) -> dict:
        data = asdict(self)
        data["signals"] = [s.as_dict() for s in self.signals[:20]]
        return data


def _clean_suggestion(raw: str, brand: str) -> str:
    """Trim a captured alternative to a plausible entity name.

    Drops the brand itself: an engine writing "did you mean Signalor?" has
    resolved the name, it is just checking spelling.
    """
    name = _TRAILING.sub("", (raw or "").strip()).strip(" .,;:\"'?!")
    if not name or len(name) < 2:
        return ""
    if name.lower() == (brand or "").strip().lower():
        return ""
    return name[:40]


def detect(text: str, brand: str) -> tuple[str, str]:
    """Classify one answer. Returns ``(kind, suggested)``; kind is "" when fine.

    A proposed correction outranks a bare "not recognised" because it carries
    the more useful information - which entity the name is being resolved to.
    """
    body = (text or "").strip()
    if not body:
        return "", ""

    for pattern in _CORRECTIONS_RE:
        match = pattern.search(body)
        if match:
            suggested = _clean_suggestion(match.group(1), brand)
            if suggested:
                return "correction", suggested

    for pattern in _UNRECOGNISED_RE:
        if pattern.search(body):
            return "unrecognised", ""

    return "", ""


# The identity question, asked plainly. Anything more elaborate invites the model
# to describe the category instead of resolving the name, which is the one thing
# this needs to observe.
IDENTITY_PROMPT = 'What is "{brand}"? Answer in two sentences.'


def probe_identity(run, engines: list[str] | None = None) -> DisambiguationReport:
    """Ask each engine who the brand is, and read the answers for confusion.

    A dedicated probe rather than a scan of stored answers. Prompt-tracking and
    visibility probes ask *category* questions ("best GEO tools"), so an engine
    can answer them fully without ever resolving the brand name - the signal
    simply is not in that text. Measuring name resolution requires asking about
    the name.

    Costs one call per engine and is therefore explicit: nothing calls this
    during a run without being asked.
    """
    from apps.analyzer.pipeline.llm import ask_answer_engines

    brand = (getattr(run, "brand_name", "") or "").strip()
    report = DisambiguationReport(brand=brand)
    if not brand:
        return report

    answers = ask_answer_engines(
        IDENTITY_PROMPT.format(brand=brand),
        engines=engines,
        purpose="Entity Disambiguation",
        max_tokens=400,
    )

    engine_totals: Counter = Counter()
    engine_confused: Counter = Counter()
    alternatives: Counter = Counter()

    for engine, payload in (answers or {}).items():
        text = (payload or {}).get("text") or ""
        if not text.strip():
            continue  # a failed call is not confusion
        engine_totals[engine] += 1
        report.responses += 1

        kind, suggested = detect(text, brand)
        if not kind:
            continue
        report.confused += 1
        engine_confused[engine] += 1
        if suggested:
            alternatives[suggested] += 1
        report.signals.append(
            ConfusionSignal(
                engine=engine,
                prompt=IDENTITY_PROMPT.format(brand=brand),
                kind=kind,
                suggested=suggested,
                excerpt=re.sub(r"\s+", " ", text)[:200],
            )
        )

    _finalize(report, engine_totals, engine_confused, alternatives, run)
    return report


def _finalize(report, engine_totals, engine_confused, alternatives, run) -> None:
    """Shared aggregation for both the probe and the passive scan."""
    if report.responses:
        report.confusion_rate = round(report.confused / report.responses, 3)
    report.by_engine = {
        engine: {
            "responses": total,
            "confused": engine_confused.get(engine, 0),
            "rate": round(engine_confused.get(engine, 0) / total, 3) if total else 0.0,
        }
        for engine, total in sorted(engine_totals.items())
    }
    report.top_alternatives = [
        {"name": name, "count": count} for name, count in alternatives.most_common(5)
    ]
    report.is_blocking = report.confusion_rate >= CONFUSION_THRESHOLD

    try:
        from apps.analyzer.pipeline.utils import check_entity_collision, extract_domain

        collided, info = check_entity_collision(report.brand, extract_domain(run.url or ""))
        report.known_collision = info if collided else None
    except Exception:
        logger.warning("entity_disambiguation: collision check failed", exc_info=True)


def analyze_run(run) -> DisambiguationReport:
    """Measure name-resolution confusion across a run's stored answers."""
    from apps.analyzer.models import PromptTrack

    brand = (getattr(run, "brand_name", "") or "").strip()
    report = DisambiguationReport(brand=brand)
    if not brand:
        return report

    engine_totals: Counter = Counter()
    engine_confused: Counter = Counter()
    alternatives: Counter = Counter()

    tracks = PromptTrack.objects.filter(analysis_run=run, deleted_at__isnull=True).prefetch_related(
        "results"
    )
    for track in tracks:
        for result in track.results.all():
            text = result.response_text or ""
            if not text.strip():
                continue  # no answer is not confusion
            engine = result.get_engine_display()
            engine_totals[engine] += 1
            report.responses += 1

            kind, suggested = detect(text, brand)
            if not kind:
                continue

            report.confused += 1
            engine_confused[engine] += 1
            if suggested:
                alternatives[suggested] += 1
            report.signals.append(
                ConfusionSignal(
                    engine=engine,
                    prompt=(track.prompt_text or "")[:160],
                    kind=kind,
                    suggested=suggested,
                    excerpt=re.sub(r"\s+", " ", text)[:200],
                )
            )

    _finalize(report, engine_totals, engine_confused, alternatives, run)
    return report


def to_recommendations(report: DisambiguationReport) -> list[dict]:
    """A task, but only when confusion is actually blocking.

    Below the threshold this is noise: every brand gets an odd answer
    occasionally, and raising a critical task for one is how a task list loses
    credibility.
    """
    if not report.is_blocking or not report.responses:
        return []

    alt = report.top_alternatives[0]["name"] if report.top_alternatives else ""
    confused_engines = sorted(
        engine for engine, stats in report.by_engine.items() if stats["confused"]
    )
    mistaken_for = f' Engines most often resolve it to "{alt}".' if alt else ""

    return [
        {
            "finding_code": "entity_unresolved",
            "pillar": "entity",
            "priority": "critical",
            "category": "entity",
            "source": "geo_signal",
            "title": f"AI engines do not recognise the name “{report.brand}”",
            "description": (
                f"{int(report.confusion_rate * 100)}% of engine answers fail to resolve "
                f"“{report.brand}” to your business - they say the term is unfamiliar or "
                f"suggest a different spelling.{mistaken_for} "
                f"Affected engines: {', '.join(confused_engines) or 'all'}."
            ),
            "action": (
                "Establish the entity before doing more content work. Create a Wikidata item; "
                "add Organization schema with sameAs links to every profile you own; use one "
                "identical name, description and category across Crunchbase, G2, LinkedIn and "
                "your own site; and always write the brand next to its category "
                f"(“{report.brand}, an AI search visibility platform”) rather than alone, "
                "so engines bind the name to the right topic."
            ),
            "why": (
                "An engine that cannot resolve the name has nothing to cite. Content and "
                "outreach both underperform until the entity exists in its world."
            ),
            "evidence": {
                "confusion_rate": report.confusion_rate,
                "responses": report.responses,
                "mistaken_for": [a["name"] for a in report.top_alternatives],
                "engines": confused_engines,
            },
            "difficulty": "medium",
            "estimated_minutes": 60,
            "xp_reward": 120,
        }
    ]


def report_for_run(run) -> dict:
    """Serializable disambiguation report. Never raises."""
    try:
        return analyze_run(run).as_dict()
    except Exception:
        logger.exception(
            "entity_disambiguation: report failed for run %s", getattr(run, "id", "?")
        )
        return DisambiguationReport(brand="").as_dict()
