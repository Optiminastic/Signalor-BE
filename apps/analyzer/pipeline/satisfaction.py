"""Task Satisfaction Gate (Phase 1).

A **suppress-only** filter between finding detection and task surfacing. For each
candidate recommendation it runs a MULTI-SIGNAL check on the already-fetched page
and drops the task only when a signal *positively* proves the work is already
done. It can never add a task, so the worst case is today's behaviour (the task
still shows) — it is safe by construction.

Why it exists: detection and the existing single-heuristic verifier share the same
blind spots, so a fix the detector's regex missed (an FAQ added via an accordion,
schema present without visible headings, a date only in JSON-LD, …) keeps a task on
the board forever. Checking several *independent* signals catches those and stops
"already-done" tasks from appearing.

Phase 1 is deterministic and runs on the crawl data already in memory — no extra
crawl, no LLM, no new model. Later phases add a persistence ledger (cross-run
memory keyed by content hash) and an LLM tier for semantic findings.

Reuses the battle-tested signal helpers from ``recommendation_verify`` rather than
re-deriving them.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from bs4 import BeautifulSoup

from apps.analyzer.recommendation_verify import (
    _count_trust_external_links,
    _has_author_bio_signal,
    _has_publish_date_signal,
    _has_updated_date_signal,
    _json_ld_blocks,
    _json_ld_types,
    _meta_robots_has_noindex,
)

logger = logging.getLogger("apps")

_ARTICLE_TYPES = {"article", "blogposting", "newsarticle"}
_HEADING_RE = re.compile(r"^h[2-4]$")
_QUESTION_HEADING_RE = re.compile(r"^h[2-5]$")


_WS_RE = re.compile(r"\s+")


def _norm(url: str) -> str:
    """Normalize a URL for map keying (trailing slash + case are not identity)."""
    return (url or "").rstrip("/").lower()


def page_content_hash(soup: BeautifulSoup) -> str:
    """Stable 16-char hash of a page's visible text — the ledger's change key.

    Hashing the normalized text (not raw HTML) means cosmetic markup churn does
    not invalidate a recorded "done" state; only real content changes do. The
    same helper is used by every writer so hashes are comparable across runs.
    """
    text = _WS_RE.sub(" ", soup.get_text(" ", strip=True)).lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class PageSignals:
    """Parsed, reusable signals for one page — built once, shared by every verifier."""

    url: str
    soup: BeautifulSoup
    jsonld_types: set[str]
    has_jsonld: bool
    content_hash: str

    @classmethod
    def from_soup(cls, url: str, soup: BeautifulSoup | None) -> PageSignals | None:
        if soup is None:
            return None
        blocks = _json_ld_blocks(soup)
        return cls(
            url=url,
            soup=soup,
            jsonld_types=_json_ld_types(blocks),
            has_jsonld=bool(blocks),
            content_hash=page_content_hash(soup),
        )

    @classmethod
    def from_crawl(cls, crawl) -> PageSignals | None:
        return cls.from_soup(getattr(crawl, "url", ""), getattr(crawl, "soup", None))

    @classmethod
    def from_html(cls, url: str, html: str) -> PageSignals | None:
        return cls.from_soup(url, BeautifulSoup(html or "", "html.parser"))


# ── Verifiers ─────────────────────────────────────────────────────────────────
# Each returns True only when the finding is POSITIVELY satisfied (→ suppress the
# task). Anything else (not satisfied, or can't tell) returns False → keep the task.


def _v_faq_section(p: PageSignals) -> bool:
    headings = p.soup.find_all(_HEADING_RE)
    if any("faq" in h.get_text(" ", strip=True).lower() for h in headings):
        return True
    questions = [h for h in p.soup.find_all(_QUESTION_HEADING_RE) if h.get_text(strip=True).endswith("?")]
    if len(questions) >= 3:
        return True
    if p.soup.find("details"):
        return True
    return bool(p.soup.select_one("[class*='accordion'], [class*='faq'], [id*='faq']"))


def _v_author(p: PageSignals) -> bool:
    meta = p.soup.find("meta", attrs={"name": "author"})
    if meta and (meta.get("content") or "").strip():
        return True
    if "person" in p.jsonld_types:
        return True
    return bool(p.soup.select_one("[rel='author'], [class*='author'], [class*='byline']"))


def _v_meta_description(p: PageSignals) -> bool:
    meta = p.soup.find("meta", attrs={"name": "description"})
    return bool(meta and (meta.get("content") or "").strip())


def _v_og_tags(p: PageSignals) -> bool:
    return bool(p.soup.find("meta", attrs={"property": re.compile(r"^og:", re.I)}))


def _v_canonical(p: PageSignals) -> bool:
    return bool(p.soup.find("link", attrs={"rel": lambda v: v and "canonical" in v}))


def _v_viewport(p: PageSignals) -> bool:
    return bool(p.soup.find("meta", attrs={"name": "viewport"}))


def _v_h1_present(p: PageSignals) -> bool:
    return len(p.soup.find_all("h1")) >= 1


def _v_single_h1(p: PageSignals) -> bool:
    return len(p.soup.find_all("h1")) <= 1


def _v_lists(p: PageSignals) -> bool:
    return bool(p.soup.find(["ul", "ol"]))


def _v_no_noindex(p: PageSignals) -> bool:
    # `meta_noindex` is satisfied once the noindex tag is gone.
    return not _meta_robots_has_noindex(p.soup)


def _types_contains(p: PageSignals, wanted: set[str]) -> bool:
    return bool(p.jsonld_types & wanted)


# finding_code → verifier. Only findings we can verify with high confidence are
# listed; everything else is left untouched (always kept).
SATISFACTION_VERIFIERS: dict[str, Callable[[PageSignals], bool]] = {
    # Content
    "no_faq_section": _v_faq_section,
    "no_h1": _v_h1_present,
    "multiple_h1": _v_single_h1,
    "no_lists": _v_lists,
    # Schema
    "no_jsonld": lambda p: p.has_jsonld,
    "no_faqpage_schema": lambda p: "faqpage" in p.jsonld_types,
    "no_organization_schema": lambda p: "organization" in p.jsonld_types,
    "no_article_schema": lambda p: _types_contains(p, _ARTICLE_TYPES),
    "no_product_schema": lambda p: "product" in p.jsonld_types,
    "no_review_schema": lambda p: _types_contains(p, {"review", "aggregaterating"}),
    "no_breadcrumb_schema": lambda p: "breadcrumblist" in p.jsonld_types,
    # E-E-A-T
    "no_author": _v_author,
    "no_author_bio": lambda p: _has_author_bio_signal(p.soup),
    "no_publish_date": lambda p: _has_publish_date_signal(p.soup),
    "no_updated_date": lambda p: _has_updated_date_signal(p.soup),
    "no_trust_links": lambda p: _count_trust_external_links(p.soup, p.url) >= 1,
    "few_external_citations": lambda p: _count_trust_external_links(p.soup, p.url) >= 3,
    # Technical
    "meta_noindex": _v_no_noindex,
    "no_meta_description": _v_meta_description,
    "no_og_tags": _v_og_tags,
    "no_canonical": _v_canonical,
    "no_viewport": _v_viewport,
}


def _is_satisfied(code: str, pages: list[PageSignals]) -> bool:
    """A task is satisfied only when a verifier positively confirms it on EVERY
    affected page — a conservative bar for a suppress-only gate."""
    verifier = SATISFACTION_VERIFIERS.get(code)
    if verifier is None or not pages:
        return False
    try:
        return all(bool(verifier(p)) for p in pages)
    except Exception:
        logger.exception("satisfaction verifier failed for %s", code)
        return False


def filter_satisfied(
    recs: list[dict], page_signals: dict[str, PageSignals]
) -> tuple[list[dict], list[dict]]:
    """Split recs into (kept, suppressed). A rec is suppressed only when a
    verifier confirms it is already done on every one of its affected pages.

    ``page_signals`` is keyed by normalized URL; recs carry ``affected_pages``.
    """
    keyed = {_norm(u): p for u, p in page_signals.items()}
    kept: list[dict] = []
    suppressed: list[dict] = []
    for rec in recs:
        affected = [keyed[_norm(u)] for u in (rec.get("affected_pages") or []) if _norm(u) in keyed]
        if _is_satisfied(rec.get("finding_code", ""), affected):
            suppressed.append(rec)
        else:
            kept.append(rec)
    return kept, suppressed
