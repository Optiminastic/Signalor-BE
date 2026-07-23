"""Grounded, per-finding marginal composite-score estimation.

Replaces the fabricated impact strings baked into ``RECOMMENDATION_RULES``
("+40% visibility", "~8 points") with a defensible number derived from the same
three layers the real scores are built from:

1. **Composite pillar weights** - ``aggregator.get_weights(industry)`` (the exact
   weights used to blend the six pillars into the composite score).
2. **Per-pillar sub-dimension weights** - ``PILLAR_SUBWEIGHTS``, mirrored from the
   weighted-total blocks inside ``content.py`` / ``eeat.py`` / ``technical.py``.
   Each scorer normalizes its sub-dimensions to 0-100 and stores them in
   ``details["checks"]["<key>_score"]``; we reuse those stored numbers as the
   live headroom signal (so the estimate reflects *this* page, not a constant).
3. **Finding -> dimension map** - ``FINDING_DIMENSION`` says which sub-dimension a
   given finding improves and ``nominal_share`` - the fraction of that dimension's
   points a fix can plausibly recover.

The module is pure and deterministic (no I/O, no DB) so it is trivially unit
tested. Findings that are not mapped, or whose dimension score is unavailable,
fall back to a coarse estimate derived from ``IMPACT_SCORES`` - and never emit a
fabricated percentage.

NOTE (drift risk): ``PILLAR_SUBWEIGHTS`` duplicates literals that live in the
scorers. ``tests/test_impact.py`` asserts the two stay in sync; update both
together if a scorer re-weights.
"""

from __future__ import annotations

from .aggregator import get_weights

# ── Sub-dimension weights, keyed by the stored ``checks`` score key ────────────
# Mirrors:
#   content.py  score_content()   (intent .30 / coverage .30 / density .20 / structure .20)
#   eeat.py     score_eeat()      (identity .25 / evidence .35 / experience .25 / trust .15)
#   technical.py score_technical() (infra .25 / perf .25 / crawl .20 / ai_read .20 / struct .10)
# schema / entity / ai_visibility use additive point buckets rather than weighted
# normalized sub-scores, so they are modelled with the bucket path below
# (score_key = None) instead of appearing here.
PILLAR_SUBWEIGHTS: dict[str, dict[str, float]] = {
    "content": {
        "intent_score": 0.30,
        "coverage_score": 0.30,
        "density_score": 0.20,
        "structure_score": 0.20,
    },
    "eeat": {
        "identity_score": 0.25,
        "evidence_score": 0.35,
        "experience_score": 0.25,
        "trust_score": 0.15,
    },
    "technical": {
        "infra_score": 0.25,
        "perf_score": 0.25,
        "crawl_score": 0.20,
        "ai_read_score": 0.20,
        "struct_score": 0.10,
    },
}

# ── Finding -> (dimension pillar, score key, recoverable share) ────────────────
# ``score_key`` is the key inside ``details[pillar]["checks"]`` holding that
# dimension's 0-100 score; ``None`` selects the bucket model (share is then a
# fraction of the *whole* pillar's 0-100 points). The dimension pillar is where
# the points actually move, which is not always the recommendation's display
# pillar (e.g. ``no_statistics`` shows under content but its score headroom lives
# in the E-E-A-T evidence dimension).
#
# ``share`` = the fraction of that dimension a single fix can plausibly recover.
FINDING_DIMENSION: dict[str, tuple[str, str | None, float]] = {
    # ── Content: intent clarity ──
    "no_h1": ("content", "intent_score", 0.35),
    "multiple_h1": ("content", "intent_score", 0.15),
    "broken_heading_hierarchy": ("content", "intent_score", 0.20),
    "no_answer_first": ("content", "intent_score", 0.25),
    # ── Content: coverage depth ──
    "no_faq_section": ("content", "coverage_score", 0.25),
    "no_citations": ("content", "coverage_score", 0.30),
    "low_word_count": ("content", "coverage_score", 0.20),
    # ── Content: information density ──
    "keyword_stuffing": ("content", "density_score", 0.35),
    "weak_authoritative_tone": ("content", "density_score", 0.30),
    "poor_readability": ("content", "density_score", 0.20),
    "no_technical_terms": ("content", "density_score", 0.15),
    "low_vocabulary_diversity": ("content", "density_score", 0.15),
    # ── Content: structure & flow ──
    "poor_paragraph_structure": ("content", "structure_score", 0.25),
    "few_internal_links": ("content", "structure_score", 0.20),
    "no_lists": ("content", "structure_score", 0.15),
    # ── E-E-A-T: identity ──
    "no_author": ("eeat", "identity_score", 0.40),
    "no_author_bio": ("eeat", "identity_score", 0.30),
    # ── E-E-A-T: evidence ──
    "few_external_citations": ("eeat", "evidence_score", 0.30),
    "no_trust_links": ("eeat", "evidence_score", 0.25),
    "no_statistics": ("eeat", "evidence_score", 0.25),
    "low_source_diversity": ("eeat", "evidence_score", 0.15),
    "low_authority": ("eeat", "evidence_score", 0.30),
    # ── E-E-A-T: experience ──
    "no_first_hand_experience": ("eeat", "experience_score", 0.40),
    "no_expert_quotes": ("eeat", "experience_score", 0.25),
    "no_publish_date": ("eeat", "experience_score", 0.15),
    "no_updated_date": ("eeat", "experience_score", 0.10),
    "no_expertise_indicators": ("eeat", "experience_score", 0.25),
    # ── E-E-A-T: trust infrastructure ──
    "no_about_page": ("eeat", "trust_score", 0.30),
    "low_trust_signals": ("eeat", "trust_score", 0.35),
    # ── Technical: infrastructure ──
    "no_llms_txt": ("technical", "infra_score", 0.35),
    "no_sitemap": ("technical", "infra_score", 0.30),
    "no_https": ("technical", "infra_score", 0.25),
    "no_viewport": ("technical", "infra_score", 0.15),
    "no_canonical": ("technical", "infra_score", 0.15),
    # ── Technical: performance ──
    "slow_load_time": ("technical", "perf_score", 0.40),
    "crawl_timeout": ("technical", "perf_score", 0.90),
    # ── Technical: crawlability ──
    "crawl_failed": ("technical", "crawl_score", 0.90),
    "crawl_blocked_403": ("technical", "crawl_score", 0.90),
    "ai_bots_blocked": ("technical", "crawl_score", 0.60),
    "meta_noindex": ("technical", "crawl_score", 0.50),
    # ── Technical: AI readability ──
    "low_text_html_ratio": ("technical", "ai_read_score", 0.40),
    "js_dependent_content": ("technical", "ai_read_score", 0.40),
    # ── Technical: structure quality (metadata) ──
    "no_meta_description": ("technical", "struct_score", 0.30),
    "no_og_tags": ("technical", "struct_score", 0.20),
    # ── Schema (bucket model - additive points, no normalized sub-score) ──
    "no_jsonld": ("schema", None, 0.35),
    "no_product_schema": ("schema", None, 0.20),
    "no_faqpage_schema": ("schema", None, 0.15),
    "no_article_schema": ("schema", None, 0.15),
    "no_organization_schema": ("schema", None, 0.15),
    "invalid_jsonld_structure": ("schema", None, 0.15),
    "no_review_schema": ("schema", None, 0.12),
    "incomplete_article_schema": ("schema", None, 0.10),
    "incomplete_organization_schema": ("schema", None, 0.10),
    "incomplete_faqpage_schema": ("schema", None, 0.10),
    "incomplete_product_schema": ("schema", None, 0.10),
    "incomplete_blogposting_schema": ("schema", None, 0.10),
    "incomplete_newsarticle_schema": ("schema", None, 0.10),
    "incomplete_howto_schema": ("schema", None, 0.08),
    "no_breadcrumb_schema": ("schema", None, 0.05),
    "no_local_business_schema": ("schema", None, 0.05),
}


def _dimension_score(pillar_details: dict, pillar: str, score_key: str) -> float | None:
    """Return the stored 0-100 sub-dimension score, or ``None`` if unavailable."""
    checks = (pillar_details.get(pillar) or {}).get("checks") or {}
    val = checks.get(score_key)
    if isinstance(val, (int, float)):
        return float(val)
    return None


def estimate_marginal_gain(
    finding_code: str,
    pillar_details: dict[str, dict],
    pillar_scores: dict[str, float] | None = None,
    *,
    industry: str = "default",
    rule_pillar: str = "",
    fallback_impact: float | None = None,
) -> dict:
    """Estimate the composite-score points a fix for ``finding_code`` can recover.

    Returns ``{"composite_points", "pillar", "pillar_points", "basis"}`` where
    ``composite_points`` is on the 0-100 composite scale and ``pillar_points`` is
    on the 0-100 pillar scale. All numbers are headroom-clamped: a fix can never
    recover points the page already holds.
    """
    scores = pillar_scores or {}
    weights = get_weights(industry)
    entry = FINDING_DIMENSION.get(finding_code)

    if entry is not None:
        pillar, score_key, share = entry
        pillar_weight = weights.get(pillar, 0.0)

        if score_key is not None:
            # Weighted normalized sub-dimension path (content / eeat / technical).
            dim_score = _dimension_score(pillar_details, pillar, score_key)
            if dim_score is not None:
                dim_weight = PILLAR_SUBWEIGHTS.get(pillar, {}).get(score_key, 0.0)
                headroom = max(0.0, 100.0 - dim_score)
                recoverable = min(share * 100.0, headroom)
                pillar_gain = (recoverable / 100.0) * dim_weight * 100.0
                composite_gain = pillar_gain * pillar_weight
                return _result(composite_gain, pillar, pillar_gain, {
                    "mode": "dimension",
                    "score_key": score_key,
                    "dim_score": round(dim_score, 1),
                    "dim_weight": dim_weight,
                    "pillar_weight": pillar_weight,
                    "headroom": round(headroom, 1),
                })
            # Sub-score not stored (partial crawl) -> pillar-headroom bucket below.

        # Bucket path: share is a fraction of the whole pillar's 0-100 points.
        pillar_headroom = max(0.0, 100.0 - float(scores.get(pillar, 0.0) or 0.0))
        pillar_gain = min(share * 100.0, pillar_headroom)
        composite_gain = pillar_gain * pillar_weight
        return _result(composite_gain, pillar, pillar_gain, {
            "mode": "bucket",
            "pillar_weight": pillar_weight,
            "headroom": round(pillar_headroom, 1),
        })

    # ── Coarse fallback: unmapped finding ──
    pillar = rule_pillar or "content"
    pillar_weight = weights.get(pillar, 0.0)
    # Treat IMPACT_SCORES (0-100 relative importance) as an approximate pillar-point
    # share, damped so unmapped findings never dominate mapped ones.
    approx = (fallback_impact if fallback_impact is not None else 10.0) / 100.0
    pillar_headroom = max(0.0, 100.0 - float(scores.get(pillar, 0.0) or 0.0))
    pillar_gain = min(approx * 20.0, pillar_headroom)  # cap coarse at ~20 pillar pts
    composite_gain = pillar_gain * pillar_weight
    return _result(composite_gain, pillar, pillar_gain, {
        "mode": "fallback",
        "pillar_weight": pillar_weight,
    })


def _result(composite: float, pillar: str, pillar_pts: float, basis: dict) -> dict:
    return {
        "composite_points": round(max(0.0, composite), 2),
        "pillar": pillar,
        "pillar_points": round(max(0.0, pillar_pts), 1),
        "basis": basis,
    }


def format_impact_estimate(gain: dict) -> str:
    """Human phrasing for the ``Recommendation.impact_estimate`` string field.

    Grounded and honest - e.g. "~1.8 points overall (+6.0 content)" - instead of
    a copied research percentage. Returns a low-signal phrase when the estimated
    gain is negligible (the page already scores well on this dimension).
    """
    composite = gain.get("composite_points", 0.0)
    pillar_pts = gain.get("pillar_points", 0.0)
    pillar = gain.get("pillar", "")
    if composite < 0.1:
        return "Minor - this area already scores well"
    pillar_label = pillar.replace("_", " ")
    return f"~{composite:g} points overall (+{pillar_pts:g} {pillar_label})"
