"""LLM enrichment of the top-ranked tasks (Phase 2).

The finding engine produces the *what*: one of 83 rules in
``pipeline/recommendations.py``, each carrying a fixed sentence of advice that is
byte-identical for every customer ("Add a single H1 tag wrapping your page
title"). This service adds the *how*, grounded in the page actually analysed, and
stores it on ``rec["generated_content"]``.

Two drafter kinds:

* **Specialised** - FAQ pairs, citation sentences, paragraph rewrite. These
  return rich structures the UI renders specially. They cover 10 finding codes.
* **Generic** - ``_enrich_generic`` rewrites any other finding's static advice
  against the real page. Before it existed the remaining 73 codes shipped
  boilerplate, which is the single biggest reason the Tasks list read like a
  stock SEO checklist rather than an analysis of the customer's site.

Design:
- Reuses existing machinery only: ``prompts.render`` (versioned Jinja2 templates),
  ``core.llm.structured.ask_structured`` (validated JSON, one repair round-trip),
  ``auto_fix._read_page_content`` (page HTML), and
  ``organizations.services.retrieval.build_knowledge_block`` (RAG brand corpus).
- Best-effort and fail-soft: any failure/refusal leaves ``generated_content = {}``
  and the static ``action`` remains the guaranteed fallback. Never raises.
- Cost-bounded: only the top-N recs (by priority) are enriched, and a
  content-hash guard skips regeneration when the page is unchanged.
- Off the request path: called from the analysis worker phase.
"""

from __future__ import annotations

import hashlib
import logging
import os

logger = logging.getLogger("apps")

# How many tasks get drafted per run. Every eligible task costs one medium-tier
# call, so this is the cost dial. It was 6 while only ~10 finding codes had a
# drafter; now that every code has one, 6 would leave most of a typical run's
# tasks showing boilerplate. Override with TASK_ENRICH_TOP_N.
TOP_N = int(os.getenv("TASK_ENRICH_TOP_N", "20"))

# Code prefix used by pipeline/site_findings.py for discovered (non-rule) findings.
_DISCOVERED_PREFIX = "site:"

# finding_code -> (template name, schema attr, content type label)
_FAQ_CODES = {"no_faq_section", "no_faqpage_schema"}
_CITATION_CODES = {"no_citations", "no_statistics", "few_external_citations", "no_trust_links"}
_REWRITE_CODES = {"poor_paragraph_structure", "low_word_count", "no_answer_first", "keyword_stuffing"}


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _render(name: str, **ctx) -> str:
    """Thin wrapper so tests can stub prompt rendering without Jinja2."""
    from apps.analyzer.prompts import render

    return render(name, **ctx)


def _prompt_version(name: str) -> str:
    try:
        from apps.analyzer.prompts import current_version

        return current_version(name)
    except Exception:
        return "v1"


def _page_content(run) -> str:
    """Fetch the run's page HTML once (public fetch; integration-agnostic)."""
    from apps.analyzer.auto_fix import _read_page_content

    try:
        return _read_page_content(None, run.url)
    except Exception:
        logger.warning("task_enrichment: page read failed for %s", run.url)
        return ""


def _brand_knowledge(run, query: str) -> str:
    from apps.organizations.services.retrieval import build_knowledge_block

    try:
        return build_knowledge_block(run, query) or ""
    except Exception:
        return ""


def _enrich_faq(run, rec, page_content, brand) -> dict | None:
    from apps.analyzer.pipeline.schemas import FaqDraft
    from core.llm.structured import ask_structured

    knowledge = _brand_knowledge(run, f"{brand} frequently asked questions")
    prompt = _render(
        "task_enrich_faq", brand=brand, url=run.url, count=5,
        page_content=page_content, brand_knowledge=knowledge or "(none provided)",
    )
    result = ask_structured(prompt, FaqDraft, tier="medium", max_tokens=900,
                            purpose="task-enrich-faq")
    if not result or not result.pairs:
        return None
    return {
        "type": "faq",
        "data": {"pairs": [p.model_dump() for p in result.pairs]},
    }


def _enrich_citations(run, rec, page_content, brand) -> dict | None:
    from apps.analyzer.pipeline.schemas import CitationSuggestions
    from core.llm.structured import ask_structured

    prompt = _render(
        "task_enrich_citations", brand=brand, url=run.url, count=4,
        page_content=page_content,
    )
    result = ask_structured(prompt, CitationSuggestions, tier="medium", max_tokens=900,
                            purpose="task-enrich-citations")
    if not result or not result.items:
        return None
    return {
        "type": "citations",
        "data": {"items": [i.model_dump() for i in result.items]},
    }


def _enrich_rewrite(run, rec, page_content, brand) -> dict | None:
    from apps.analyzer.pipeline.schemas import ParagraphRewrite
    from core.llm.structured import ask_structured

    hint = (rec.get("evidence") or {}).get("top_repeated", "")
    prompt = _render(
        "task_enrich_rewrite", brand=brand, url=run.url,
        title=rec.get("title", ""), description=rec.get("description", ""),
        hint=hint, page_content=page_content,
    )
    result = ask_structured(prompt, ParagraphRewrite, tier="medium", max_tokens=900,
                            purpose="task-enrich-rewrite")
    if not result or not result.rewritten:
        return None
    return {
        "type": "rewrite",
        "data": {"original": result.original, "rewritten": result.rewritten},
    }


def _enrich_generic(run, rec, page_content, brand) -> dict | None:
    """Draft a page-specific version of any finding's static advice.

    This is what stops the other ~73 findings shipping identical boilerplate.
    The specialised drafters above stay in front of it because they return
    richer structures (Q&A pairs, citation sentences, a rewrite); this one
    handles everything else.
    """
    from apps.analyzer.pipeline.schemas import TaskGuidance
    from core.llm.structured import ask_structured

    title = rec.get("title", "")
    evidence = rec.get("evidence") or {}
    prompt = _render(
        "task_enrich_generic",
        brand=brand,
        url=run.url,
        title=title,
        description=rec.get("description", ""),
        action=rec.get("action", ""),
        evidence=_evidence_line(evidence),
        page_content=page_content,
        brand_knowledge=_brand_knowledge(run, f"{brand} {title}"),
    )
    result = ask_structured(
        prompt, TaskGuidance, tier="medium", max_tokens=900, purpose="task-enrich-generic"
    )
    if not result or not result.steps:
        return None
    return {
        "type": "guidance",
        "data": {
            "observation": result.observation,
            "steps": result.steps,
            "snippet": result.snippet,
        },
    }


def _evidence_line(evidence: dict) -> str:
    """Flatten the finding's evidence dict into one prompt-friendly line."""
    if not isinstance(evidence, dict):
        return ""
    parts = [f"{k}: {v}" for k, v in evidence.items() if v not in (None, "", [], {})]
    return "; ".join(parts)[:400]


def _dispatch(code: str):
    # Findings discovered by pipeline/site_findings.py are born page-specific and
    # already carry their own generated_content. Re-drafting them would spend a
    # call to replace grounded, evidence-checked text with a generic rewrite.
    if code.startswith(_DISCOVERED_PREFIX):
        return None, None
    if code in _FAQ_CODES:
        return "task_enrich_faq", _enrich_faq
    if code in _CITATION_CODES:
        return "task_enrich_citations", _enrich_citations
    if code in _REWRITE_CODES:
        return "task_enrich_rewrite", _enrich_rewrite
    # Everything else gets the generic drafter rather than static template text.
    return "task_enrich_generic", _enrich_generic


def enrich_recommendations(run, recs: list[dict], *, top_n: int | None = None) -> None:
    """Draft concrete fix content for the top-``top_n`` enrichable recs, in place.

    Mutates ``rec["generated_content"]``. Fail-soft per rec: a failure leaves the
    empty dict and the static template stands.
    """
    limit = TOP_N if top_n is None else top_n
    page_hash = _content_hash(getattr(run, "content_hash", "") or run.url)

    # Enrich the highest-priority tasks we have a drafter for.
    _sev = {"critical": 3, "high": 2, "medium": 1}
    enrichable = [r for r in recs if _dispatch(r.get("finding_code", ""))[1] is not None]
    enrichable.sort(key=lambda r: -_sev.get(r.get("priority", "low"), 0))
    targets = enrichable[:limit]
    if not targets:
        return
    if len(enrichable) > limit:
        # Say what was left generic rather than letting it look fully covered.
        logger.info(
            "task_enrichment: enriching %d of %d eligible tasks for %s (cap=%d); "
            "the rest keep their static text",
            limit,
            len(enrichable),
            run.url,
            limit,
        )

    page_content = _page_content(run)
    if not page_content:
        logger.info("task_enrichment: no page content for %s; skipping enrichment", run.url)
        return

    brand = run.brand_name or "the website"
    for rec in targets:
        code = rec.get("finding_code", "")
        template_name, drafter = _dispatch(code)

        # Skip if we already drafted this exact page content for this task.
        existing = rec.get("generated_content") or {}
        if existing.get("content_hash") == page_hash and existing.get("data"):
            continue

        try:
            drafted = drafter(run, rec, page_content, brand)
        except Exception:
            logger.exception("task_enrichment: drafting failed for %s", code)
            drafted = None

        if drafted:
            drafted.update({
                "prompt_version": _prompt_version(template_name),
                "content_hash": page_hash,
            })
            rec["generated_content"] = drafted
