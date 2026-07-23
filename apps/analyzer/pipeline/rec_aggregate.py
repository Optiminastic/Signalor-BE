"""Run-level recommendation assembly: evidence grounding + cross-page dedupe.

The per-page ``generate_recommendations`` engine (recommendations.py) produces
one template per finding. Historically only the homepage's findings reached it,
and a finding present on several pages produced several byte-identical tasks.

This module fixes both:

- ``attach_evidence`` pulls the concrete numbers behind a finding out of the
  scorer's ``details["checks"]`` tree (e.g. ``citation_count``, ``word_count``,
  ``top_repeated``) so the task can be grounded in *this* page's reality.
- ``dedupe_recommendations`` collapses the same finding across N pages into one
  task carrying an ``affected_pages`` list.
- ``build_run_recommendations`` is the single orchestrator both analysis call
  sites use: it runs the engine over the homepage and every additional page,
  grounds + dedupes, and re-applies the per-pillar / total caps post-dedupe.

Pure functions (no DB, no I/O) so the assembly logic is unit-testable.
"""

from __future__ import annotations

from collections.abc import Callable

from .recommendations import (
    MAX_PER_PILLAR,
    MAX_RECOMMENDATIONS,
    PRIORITY_ORDER,
    generate_recommendations,
)

# Finding -> the ``checks`` keys worth surfacing as grounding evidence. Keys are
# looked up recursively across the pillar details tree, so nesting under a
# sub-dimension dict (content) or a flat layout (eeat) both resolve. Missing keys
# are simply omitted - a finding with no evidence falls back to the static copy.
EVIDENCE_KEYS: dict[str, list[str]] = {
    # ── Content ──
    "no_h1": ["has_clear_title", "subheading_count"],
    "multiple_h1": ["subheading_count"],
    "broken_heading_hierarchy": ["heading_hierarchy_ok", "heading_count"],
    "no_faq_section": ["has_faq", "unique_sections"],
    "no_citations": ["citation_count", "stat_count", "word_count"],
    "low_word_count": ["word_count", "unique_sections"],
    "keyword_stuffing": ["top_repeated", "repetition_ratio"],
    "weak_authoritative_tone": ["hedging_signals", "authority_signals"],
    "low_vocabulary_diversity": ["vocabulary_ttr"],
    "poor_readability": ["fk_grade"],
    "poor_paragraph_structure": ["avg_paragraph_words", "paragraph_count"],
    "few_internal_links": ["internal_link_count"],
    "no_lists": ["list_count"],
    # ── E-E-A-T ──
    "no_author": ["author_found", "author_name"],
    "no_author_bio": ["author_bio"],
    "no_trust_links": ["trust_link_count", "external_link_count"],
    "low_source_diversity": ["source_diversity"],
    "no_statistics": ["statistic_count"],
    "few_external_citations": ["external_link_count", "trust_link_count"],
    "no_first_hand_experience": ["experience_phrases", "specific_results"],
    "no_expert_quotes": ["expert_quotes"],
    "no_publish_date": ["publish_date"],
    "no_updated_date": ["updated_date"],
    "no_about_page": ["has_about_page", "has_contact_page"],
}


# Per-finding grounding sentence built from collected evidence. Each formatter is
# called only when its evidence dict is non-empty and returns "" to opt out (so a
# missing/edge value never produces a broken sentence). ASCII only.
def _n(v, default=0):
    return v if isinstance(v, (int, float)) else default


GROUNDING: dict[str, Callable[[dict], str]] = {
    "no_citations": lambda e: (
        f"This page has {_n(e.get('citation_count'))} citations across "
        f"{_n(e.get('word_count'))} words."
        if e.get("word_count") is not None else ""
    ),
    "low_word_count": lambda e: (
        f"This page has only {_n(e.get('word_count'))} words."
        if e.get("word_count") is not None else ""
    ),
    "keyword_stuffing": lambda e: (
        f"The phrase \"{e.get('top_repeated')}\" is over-repeated."
        if e.get("top_repeated") else ""
    ),
    "poor_paragraph_structure": lambda e: (
        f"Paragraphs average {_n(e.get('avg_paragraph_words'))} words."
        if e.get("avg_paragraph_words") else ""
    ),
    "few_internal_links": lambda e: (
        f"Only {_n(e.get('internal_link_count'))} internal links found."
        if e.get("internal_link_count") is not None else ""
    ),
    "no_statistics": lambda e: (
        f"Only {_n(e.get('statistic_count'))} statistics detected."
        if e.get("statistic_count") is not None else ""
    ),
    "no_trust_links": lambda e: (
        f"{_n(e.get('trust_link_count'))} links to high-trust domains found."
        if e.get("trust_link_count") is not None else ""
    ),
    "no_first_hand_experience": lambda e: (
        f"{_n(e.get('experience_phrases'))} first-hand experience phrases detected."
        if e.get("experience_phrases") is not None else ""
    ),
}


def ground_description(rec: dict) -> dict:
    """Prepend a concrete, page-specific sentence to a rec's description."""
    fmt = GROUNDING.get(rec.get("finding_code", ""))
    if not fmt:
        return rec
    try:
        sentence = fmt(rec.get("evidence") or {})
    except Exception:
        sentence = ""
    if sentence:
        rec["description"] = f"{sentence} {rec.get('description', '')}".strip()
    return rec


def _collect(node: object, keys: set[str], out: dict) -> None:
    """Recursively pull the first occurrence of each wanted key from a checks tree."""
    if not isinstance(node, dict):
        return
    for k, v in node.items():
        if k in keys and k not in out:
            out[k] = v
        elif isinstance(v, dict):
            _collect(v, keys, out)


def attach_evidence(rec: dict, pillar_details: dict[str, dict]) -> dict:
    """Populate ``rec["evidence"]`` from the scorers' checks for this finding.

    Searches every provided pillar's ``checks`` tree because a finding's evidence
    does not always live under the recommendation's display pillar.
    """
    keys = set(EVIDENCE_KEYS.get(rec.get("finding_code", ""), []))
    if not keys:
        rec.setdefault("evidence", {})
        return rec
    found: dict = {}
    for details in pillar_details.values():
        _collect((details or {}).get("checks") or {}, keys, found)
    rec["evidence"] = found
    return rec


def _evidence_richness(rec: dict) -> int:
    return len(rec.get("evidence") or {})


def dedupe_recommendations(tagged_recs: list[dict]) -> list[dict]:
    """Collapse recs sharing a ``finding_code`` into one, keeping the strongest.

    "Strongest" = highest ``impact_points``, tie-broken by richer evidence. The
    survivor gains ``affected_pages`` (sorted unique page URLs); its count is
    derivable as ``len(affected_pages)``. The transient ``_page_url`` tag is
    removed so the dict stays a clean ``Recommendation`` kwargs mapping.
    """
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for rec in tagged_recs:
        code = rec.get("finding_code") or rec.get("title", "")
        if code not in groups:
            groups[code] = []
            order.append(code)
        groups[code].append(rec)

    deduped: list[dict] = []
    for code in order:
        members = groups[code]
        winner = max(
            members,
            key=lambda r: (r.get("impact_points", 0.0), _evidence_richness(r)),
        )
        pages = sorted({r.get("_page_url") for r in members if r.get("_page_url")})
        winner["affected_pages"] = pages
        winner.pop("_page_url", None)
        deduped.append(winner)
    return deduped


def _cap(recs: list[dict]) -> list[dict]:
    """Re-apply per-pillar and total caps after per-page recs were merged."""
    recs.sort(
        key=lambda r: (
            -r.get("impact_points", 0.0),
            PRIORITY_ORDER.get(r.get("priority", "low"), 3),
        )
    )
    per_pillar: dict[str, int] = {}
    kept: list[dict] = []
    for rec in recs:
        p = rec.get("pillar", "")
        if per_pillar.get(p, 0) >= MAX_PER_PILLAR:
            continue
        per_pillar[p] = per_pillar.get(p, 0) + 1
        kept.append(rec)
    return kept[:MAX_RECOMMENDATIONS]


def build_run_recommendations(
    homepage_details: dict[str, dict],
    page_scores_data: list[dict] | None,
    pillar_scores: dict[str, float] | None,
    *,
    industry: str = "default",
    run_url: str = "",
    extra_recs: list[dict] | None = None,
) -> list[dict]:
    """Assemble a run's grounded, deduped, capped recommendation set.

    1. Run the engine over the homepage and each additional page (content+schema).
    2. Ground each rec in its page's evidence and tag its source page.
    3. Fold in ``extra_recs`` (e.g. SiteOne findings) *before* dedupe.
    4. Dedupe across pages, then re-apply caps.
    """
    scores = pillar_scores or {}
    tagged: list[dict] = []

    def _emit(details: dict, page_scores: dict, url: str) -> None:
        for rec in generate_recommendations(details, page_scores, industry):
            attach_evidence(rec, details)
            ground_description(rec)
            rec["_page_url"] = url
            tagged.append(rec)

    # Homepage (all six pillars).
    _emit(homepage_details, scores, run_url)

    # Additional pages carry only content + schema details (see tasks.py).
    for page in page_scores_data or []:
        page_details = {
            "content": page.get("content_details") or {},
            "schema": page.get("schema_details") or {},
        }
        page_scores = {
            **scores,
            "content": page.get("content_score", scores.get("content", 0)),
            "schema": page.get("schema_score", scores.get("schema", 0)),
        }
        _emit(page_details, page_scores, page.get("url") or run_url)

    # SiteOne / other pre-built recs join the pool before dedupe so duplicates
    # against pipeline findings collapse too. Tag them to the site root.
    for rec in extra_recs or []:
        rec.setdefault("_page_url", run_url)
        rec.setdefault("impact_points", 0.0)
        rec.setdefault("evidence", {})
        tagged.append(rec)

    return _cap(dedupe_recommendations(tagged))
