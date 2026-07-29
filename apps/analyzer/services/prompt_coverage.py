"""Prompt → Page coverage: which tracked prompts the site actually answers.

An answer engine retrieves *passages*, so before authority or off-page work can
matter there has to be a passage on the site that plausibly answers the prompt.
Prompt tracking measures the outcome ("we were not cited") and the recommendation
engine measures pages in isolation ("this page has no H1"). Neither answers the
question a customer actually needs first: **for this prompt, do we even have a
page?** A prompt with no answering content cannot be fixed by any on-page task;
it needs a page written.

Method: every crawled page is already embedded into ``BrandCorpusChunk`` for RAG,
so coverage is a semantic search per prompt against the brand's own corpus. The
best-matching chunk's ``source_url`` is the page that would be retrieved, and its
cosine similarity is how well it answers. No new crawling, no new embeddings, no
new model calls beyond one query embedding per prompt.

Coverage bands are deliberately coarse. The output is a work queue - "these six
prompts have nothing" - not a metric anyone should optimise to three decimals.

**Unknown is not uncovered.** An empty corpus means the knowledge base has not
been ingested yet, which is a completely different situation from a site that
genuinely fails to answer its prompts. Reporting the first as "0% covered" would
send a customer off writing pages they may already have.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

logger = logging.getLogger("apps")

# Cosine similarity bands. Calibrated to be forgiving: the cost of wrongly telling
# someone to write a page they already have is higher than the cost of a soft
# "strengthen this one".
STRONG_MATCH = 0.62
WEAK_MATCH = 0.45

# Chunks to pull per prompt. Coverage only needs the best few; a wider net costs
# retrieval time without changing which page wins.
CHUNKS_PER_PROMPT = 5

COVERED = "covered"
WEAK = "weak"
UNCOVERED = "uncovered"
UNKNOWN = "unknown"

_STATUS_GUIDANCE = {
    COVERED: "A page already answers this. Improve its extractability rather than writing a new one.",
    WEAK: "A page is related but does not answer this directly. Add an answer-first section to it.",
    UNCOVERED: "Nothing on the site answers this. This prompt needs a page written for it.",
    UNKNOWN: "Coverage could not be determined - the site's knowledge base has not been indexed yet.",
}


@dataclass
class PromptCoverage:
    prompt_id: int
    prompt_text: str
    intent: str
    status: str
    best_url: str = ""
    best_score: float = 0.0
    best_heading: str = ""
    supporting_urls: list[str] = field(default_factory=list)
    guidance: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _classify(score: float) -> str:
    if score >= STRONG_MATCH:
        return COVERED
    if score >= WEAK_MATCH:
        return WEAK
    return UNCOVERED


def _corpus_is_populated(run) -> bool:
    """Whether this org has any indexed page content to match against."""
    from apps.organizations.models import BrandCorpusChunk

    org = getattr(run, "organization", None)
    if org is None:
        return False
    return BrandCorpusChunk.objects.filter(
        organization=org, is_current=True, embedding__isnull=False
    ).exists()


def coverage_for_run(run, prompts=None) -> list[PromptCoverage]:
    """Coverage for each tracked prompt on ``run``, best-answered first.

    Never raises: retrieval is already fail-soft, and a coverage report failing
    must not take down whatever page is rendering it.
    """
    from apps.analyzer.models import PromptTrack
    from apps.organizations.services.retrieval import retrieve

    if prompts is None:
        prompts = list(
            PromptTrack.objects.filter(analysis_run=run, deleted_at__isnull=True).order_by("id")
        )
    if not prompts:
        return []

    indexed = _corpus_is_populated(run)
    if not indexed:
        logger.info(
            "prompt_coverage: no indexed corpus for run %s; reporting unknown rather than uncovered",
            getattr(run, "id", "?"),
        )

    rows: list[PromptCoverage] = []
    for prompt in prompts:
        text = (prompt.prompt_text or "").strip()
        if not text:
            continue

        if not indexed:
            rows.append(
                PromptCoverage(
                    prompt_id=prompt.id,
                    prompt_text=text,
                    intent=prompt.intent or "",
                    status=UNKNOWN,
                    guidance=_STATUS_GUIDANCE[UNKNOWN],
                )
            )
            continue

        chunks = retrieve(run, text, k=CHUNKS_PER_PROMPT)
        if not chunks:
            rows.append(
                PromptCoverage(
                    prompt_id=prompt.id,
                    prompt_text=text,
                    intent=prompt.intent or "",
                    status=UNCOVERED,
                    guidance=_STATUS_GUIDANCE[UNCOVERED],
                )
            )
            continue

        best = max(chunks, key=lambda c: c.score)
        status = _classify(best.score)
        # Distinct pages beyond the winner, so the UI can show "3 pages touch this".
        supporting = []
        for chunk in chunks:
            if chunk.source_url and chunk.source_url != best.source_url:
                if chunk.source_url not in supporting:
                    supporting.append(chunk.source_url)

        rows.append(
            PromptCoverage(
                prompt_id=prompt.id,
                prompt_text=text,
                intent=prompt.intent or "",
                status=status,
                best_url=best.source_url,
                best_score=round(best.score, 4),
                best_heading=" > ".join(best.heading_path or [])[:200],
                supporting_urls=supporting[:3],
                guidance=_STATUS_GUIDANCE[status],
            )
        )

    rows.sort(key=lambda r: -r.best_score)
    return rows


def summarize(rows: list[PromptCoverage]) -> dict:
    """Headline counts for the coverage widget.

    ``coverage_pct`` is over *measurable* prompts only. Including unknowns would
    read as poor coverage when the real problem is a missing index.
    """
    counts = {COVERED: 0, WEAK: 0, UNCOVERED: 0, UNKNOWN: 0}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1

    measurable = counts[COVERED] + counts[WEAK] + counts[UNCOVERED]
    return {
        "total_prompts": len(rows),
        "covered": counts[COVERED],
        "weak": counts[WEAK],
        "uncovered": counts[UNCOVERED],
        "unknown": counts[UNKNOWN],
        "measurable": measurable,
        "coverage_pct": round(100 * counts[COVERED] / measurable, 1) if measurable else None,
        # The work queue: what to write next, worst first.
        "needs_page": [r.prompt_text for r in rows if r.status == UNCOVERED][:10],
        "needs_section": [
            {"prompt": r.prompt_text, "url": r.best_url} for r in rows if r.status == WEAK
        ][:10],
    }


def report_for_run(run) -> dict:
    """Full coverage report: rows plus summary. Safe for direct serialization."""
    try:
        rows = coverage_for_run(run)
    except Exception:
        logger.exception("prompt_coverage: report failed for run %s", getattr(run, "id", "?"))
        return {"rows": [], "summary": summarize([])}
    return {"rows": [r.as_dict() for r in rows], "summary": summarize(rows)}
