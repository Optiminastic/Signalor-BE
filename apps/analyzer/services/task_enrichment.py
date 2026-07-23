"""LLM enrichment of the top-ranked tasks (Phase 2).

The finding engine produces the *what* (a generic template). This service adds
the *how* for the highest-impact tasks: concrete, page-specific, brand-grounded
content the user can paste - drafted FAQ Q&A, citation sentences, or a rewritten
paragraph - stored on ``rec["generated_content"]``.

Design:
- Reuses existing machinery only: ``prompts.render`` (versioned Jinja2 templates),
  ``pipeline.structured.ask_structured`` (validated JSON, one repair round-trip),
  ``auto_fix._read_page_content`` (page HTML), and
  ``organizations.services.retrieval.build_knowledge_block`` (RAG brand corpus).
- Best-effort and fail-soft: any failure/refusal leaves ``generated_content = {}``
  and the static ``action`` remains the guaranteed fallback. Never raises.
- Cost-bounded: only the top-N recs (by ``impact_points``) are enriched, and a
  content-hash guard skips regeneration when the page is unchanged.
- Off the request path: called from the analysis worker phase.
"""

from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger("apps")

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
    from apps.analyzer.pipeline.structured import ask_structured

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
    from apps.analyzer.pipeline.structured import ask_structured

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
    from apps.analyzer.pipeline.structured import ask_structured

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


def _dispatch(code: str):
    if code in _FAQ_CODES:
        return "task_enrich_faq", _enrich_faq
    if code in _CITATION_CODES:
        return "task_enrich_citations", _enrich_citations
    if code in _REWRITE_CODES:
        return "task_enrich_rewrite", _enrich_rewrite
    return None, None


def enrich_recommendations(run, recs: list[dict], *, top_n: int = 6) -> None:
    """Draft concrete fix content for the top-``top_n`` enrichable recs, in place.

    Mutates ``rec["generated_content"]``. Fail-soft per rec: a failure leaves the
    empty dict and the static template stands.
    """
    page_hash = _content_hash(getattr(run, "content_hash", "") or run.url)

    # Rank by grounded impact; only enrich the codes we have a drafter for.
    enrichable = [r for r in recs if _dispatch(r.get("finding_code", ""))[1] is not None]
    enrichable.sort(key=lambda r: -r.get("impact_points", 0.0))
    targets = enrichable[:top_n]
    if not targets:
        return

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
