"""Answer blocks: paste-ready content that makes a page answer a tracked prompt.

The rest of the product tells a customer *what* is wrong. This writes the thing
that fixes it. For one prompt the site is losing, it produces the passage to put
on the page: a question-shaped heading, a direct answer in the first two or three
sentences, self-contained supporting points, follow-up FAQs, and valid FAQPage
JSON-LD.

The unit of work is a passage, not a page, because that is what engines retrieve.
"Improve this page" is not actionable; "paste these four sentences under the hero"
is.

Two design choices worth keeping:

* **Coverage decides the mode.** ``prompt_coverage`` already knows whether a page
  exists for the prompt. If one does, this drafts a section to add to it; if not,
  it drafts the opening of a new page. Same prompt, different instruction, and
  the caller does not have to decide.
* **Schema is built in Python, not written by the model.** JSON-LD is a format a
  parser should emit exactly. Asking a language model for it invites subtly
  invalid output that fails silently in a validator months later, so the model
  supplies the Q&A pairs and ``build_faq_jsonld`` assembles them.
"""

from __future__ import annotations

import html
import json
import logging
from dataclasses import asdict, dataclass, field

logger = logging.getLogger("apps")

# How much of the target page to show. Enough for the model to stay consistent
# with what is already there, without paying for the whole document.
PAGE_EXCERPT_CHARS = 3000
BRAND_KNOWLEDGE_CHARS = 1200


@dataclass
class AnswerBlockDraft:
    prompt: str
    heading: str
    answer: str
    supporting_points: list[str] = field(default_factory=list)
    faqs: list[dict] = field(default_factory=list)
    placement: str = ""
    target_url: str = ""
    mode: str = "new_page"  # "add_section" when a page already exists
    faq_jsonld: str = ""
    html_snippet: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def build_faq_jsonld(faqs: list[dict], *, wrap: bool = True) -> str:
    """Valid schema.org FAQPage JSON-LD from drafted Q&A pairs.

    Built deterministically rather than generated: this is a serialization task
    with one correct answer, and a model that emits *almost* valid JSON-LD
    produces a bug nobody notices until a validator flags it.
    """
    pairs = [
        f
        for f in (faqs or [])
        if (f.get("question") or "").strip() and (f.get("answer") or "").strip()
    ]
    if not pairs:
        return ""

    payload = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": f["question"].strip(),
                "acceptedAnswer": {"@type": "Answer", "text": f["answer"].strip()},
            }
            for f in pairs
        ],
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    if not wrap:
        return body
    return f'<script type="application/ld+json">\n{body}\n</script>'


def build_html_snippet(draft: AnswerBlockDraft) -> str:
    """The block as pasteable HTML.

    Heading level is h2 when adding to an existing page (that page already has an
    h1) and h1 for a new page, so pasting the output never breaks the document
    outline - a real extractability problem, not a cosmetic one.
    """
    tag = "h2" if draft.mode == "add_section" else "h1"
    parts = [
        f"<{tag}>{html.escape(draft.heading)}</{tag}>",
        f"<p>{html.escape(draft.answer)}</p>",
    ]
    if draft.supporting_points:
        items = "\n".join(f"  <li>{html.escape(p)}</li>" for p in draft.supporting_points)
        parts.append(f"<ul>\n{items}\n</ul>")
    for faq in draft.faqs:
        q, a = (faq.get("question") or "").strip(), (faq.get("answer") or "").strip()
        if q and a:
            parts.append(f"<h3>{html.escape(q)}</h3>\n<p>{html.escape(a)}</p>")
    return "\n\n".join(parts)


def _page_excerpt(url: str) -> str:
    if not url:
        return ""
    try:
        from apps.analyzer.pipeline.crawler import crawl_page

        crawl = crawl_page(url)
        if not crawl.ok:
            return ""
        import re

        return re.sub(r"\s+", " ", crawl.text or "").strip()[:PAGE_EXCERPT_CHARS]
    except Exception:
        logger.warning("answer_block: page fetch failed for %s", url, exc_info=True)
        return ""


def _brand_knowledge(run, query: str) -> str:
    try:
        from apps.organizations.services.retrieval import build_knowledge_block

        return (build_knowledge_block(run, query, max_chars=BRAND_KNOWLEDGE_CHARS) or "")[
            :BRAND_KNOWLEDGE_CHARS
        ]
    except Exception:
        return ""


def generate(run, prompt_text: str, *, target_url: str = "", intent: str = "") -> AnswerBlockDraft | None:
    """Draft the answer block for one prompt. ``None`` if it could not be written.

    ``target_url`` is the page to extend; omit it to draft a new page's opening.
    Fail-soft: a failure here costs a draft, never a run.
    """
    from apps.analyzer.pipeline.schemas import AnswerBlock
    from apps.analyzer.pipeline.structured import ask_structured
    from apps.analyzer.prompts import render

    text = (prompt_text or "").strip()
    if not text:
        return None

    page_content = _page_excerpt(target_url)
    # A URL whose content we could not fetch is not a usable target: drafting an
    # "add a section to this page" block against no page content would invent
    # what the page says.
    has_page = bool(page_content)

    try:
        rendered = render(
            "answer_block",
            brand=(getattr(run, "brand_name", "") or "the brand"),
            prompt=text,
            intent=intent or "",
            url=target_url or "",
            has_page=has_page,
            page_content=page_content or "(none)",
            brand_knowledge=_brand_knowledge(run, text),
        )
    except Exception:
        logger.exception("answer_block: prompt render failed")
        return None

    result = ask_structured(
        rendered, AnswerBlock, tier="medium", max_tokens=1400, purpose="answer-block"
    )
    if not result or not (result.answer or "").strip():
        logger.info("answer_block: model returned nothing usable for %r", text[:60])
        return None

    draft = AnswerBlockDraft(
        prompt=text,
        heading=(result.heading or text).strip(),
        answer=result.answer.strip(),
        supporting_points=[p.strip() for p in (result.supporting_points or []) if p.strip()],
        faqs=[
            {"question": f.question.strip(), "answer": f.answer.strip()}
            for f in (result.faqs or [])
            if f.question.strip() and f.answer.strip()
        ],
        placement=(result.placement or "").strip(),
        target_url=target_url if has_page else "",
        mode="add_section" if has_page else "new_page",
    )
    draft.faq_jsonld = build_faq_jsonld(draft.faqs)
    draft.html_snippet = build_html_snippet(draft)
    return draft


def generate_for_prompt(track) -> dict | None:
    """Draft the answer block for a ``PromptTrack``, choosing the mode from coverage.

    Coverage already knows whether a page answers this prompt, so the caller does
    not have to: a covered or weak prompt gets a section for the page it matched,
    an uncovered one gets a new page's opening.
    """
    from apps.analyzer.services.prompt_coverage import UNCOVERED, UNKNOWN, coverage_for_run

    run = track.analysis_run
    target_url = ""
    try:
        rows = coverage_for_run(run, prompts=[track])
        if rows and rows[0].status not in {UNCOVERED, UNKNOWN}:
            target_url = rows[0].best_url
    except Exception:
        logger.warning("answer_block: coverage lookup failed; drafting a new page", exc_info=True)

    draft = generate(
        run,
        track.prompt_text,
        target_url=target_url,
        intent=getattr(track, "intent", "") or "",
    )
    return draft.as_dict() if draft else None
