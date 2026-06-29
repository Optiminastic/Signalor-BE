"""
GEO Content Hardener — turn the three highest-impact-yet-unfixable content
findings into grounded, verbatim text edits.

Why this module exists
----------------------
Our own recommendation catalog ranks these three content findings as the biggest
levers for AI visibility (impact scores 95 / 90 / 75 — the top of the list):

    no_citations     +40% visibility
    no_statistics    +37% visibility
    no_expert_quotes +30% visibility

Yet every "doer" we ship fixes the *small* pillars (schema, meta, robots), and the
GitHub fix agent is *explicitly forbidden* from touching these three — its system
prompt says "NEVER fabricate facts … Do NOT invent expert quotes, statistics,
research figures, citations …". So the agent always calls cannot_fix on exactly
the findings that matter most. Nothing closes that gap.

This module supplies the missing piece: a **grounded research step** that fetches
REAL, source-cited facts, then weaves them into the page's existing copy
**additively**, behind **pure no-fabrication guards**. Because the facts come
with verifiable source URLs (Perplexity citations) and every guard is checked at
insertion time, we can add citations / statistics / quotes *without inventing
anything*.

Output shape
------------
``harden()`` returns verbatim ``{kind, original, new}`` content edits — the exact
shape ``apps.github_agent.services.agent.generate_content_edits`` already applies
(branch + commit + PR), and that ``apps.analyzer.auto_fix`` sends to the
WP/Shopify plugin. So the whole apply/verify pipeline is reused; this module only
adds the grounded *generation*.

Testability
-----------
All network lives behind the injected ``research`` and ``propose`` callables.
``validate_edits`` — the safety-critical core — is a **pure function** with no
network and no LLM, unit-tested with hand-built evidence (see tests.py).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

logger = logging.getLogger("apps")

# finding_code -> the kind of evidence that fixes it
KIND_BY_FINDING: dict[str, str] = {
    "no_citations": "citation",
    "no_statistics": "statistic",
    "no_expert_quotes": "quote",
}

# Guards
MIN_BODY_CHARS = 200  # too little copy to harden meaningfully
MAX_EDITS = 3  # at most a few insertions per pass — keep PRs reviewable
MAX_GROWTH_CHARS = 1500  # one hardened paragraph shouldn't balloon beyond this
MIN_EVIDENCE = 1  # need at least one grounded source to do anything

_URL_RE = re.compile(r"https?://[^\s)\"'<>]+")
# Number-like tokens: 37, 1,200, 3.5, 40%, $4.2 … (leading $ handled by stripping)
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
@dataclass
class Evidence:
    """One grounded fact from the research step. ``source_url`` is verifiable."""

    kind: str  # statistic | citation | quote
    statement: str  # the fact, in words
    value: str  # the figure / quote text (may be "")
    source_url: str
    source_title: str = ""


@dataclass
class HardenEdit:
    """A verbatim additive edit: ``new`` contains ``original`` plus woven-in evidence."""

    original: str
    new: str
    kind: str = "text"
    evidence_urls: list[str] = field(default_factory=list)
    summary: str = ""

    def as_content_edit(self) -> dict:
        """Shape consumed by github_agent.agent.generate_content_edits / auto_fix."""
        return {"kind": "text", "original": self.original, "new": self.new}


@dataclass
class HardenProposal:
    finding_code: str
    page_url: str
    edits: list[HardenEdit] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)  # {original, new, reason}
    note: str = ""  # human-readable status when there's nothing to apply

    @property
    def ok(self) -> bool:
        return bool(self.edits)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _norm_url(url: str) -> str:
    """Normalise for set-membership: lowercase host, drop scheme/query/fragment/trailing slash."""
    try:
        p = urlparse(url.strip())
        host = (p.netloc or "").lower().removeprefix("www.")
        path = (p.path or "").rstrip("/")
        return f"{host}{path}" if host else url.strip().lower()
    except Exception:
        return url.strip().lower()


def _numbers(text: str) -> set[str]:
    """Normalised numeric tokens in ``text`` (commas stripped so 1,200 == 1200)."""
    return {m.group(0).replace(",", "") for m in _NUM_RE.finditer(text)}


def _evidence_index(evidence: list[Evidence]) -> tuple[set[str], str]:
    """Return (allowed normalised URLs, lowercased blob of all evidence text)."""
    urls = {_norm_url(e.source_url) for e in evidence if e.source_url}
    blob = " ".join(f"{e.statement} {e.value} {e.source_url}" for e in evidence).lower()
    return urls, blob


# --------------------------------------------------------------------------- #
# the safety-critical core — PURE, no network, no LLM
# --------------------------------------------------------------------------- #
def validate_edits(
    raw_edits: list[dict],
    evidence: list[Evidence],
    body_text: str,
) -> tuple[list[HardenEdit], list[dict]]:
    """Accept only grounded, additive, non-fabricating edits.

    Returns ``(accepted, rejected)`` where rejected items are
    ``{original, new, reason}``. Every guard here is a hard reject — a wrong or
    invented fact is worse than leaving the finding manual.

    Guards (in order):
      1. Shape — non-empty ``original`` and ``new`` strings; ``new`` != ``original``.
      2. Targets real copy — ``original`` is a substring of the live page body.
      3. Additive-only — ``original`` is a substring of ``new`` (we only ADD; we
         never rewrite away or delete existing copy). Conservative by design.
      4. Bounded growth — ``new`` doesn't shrink and doesn't balloon past
         MAX_GROWTH_CHARS over ``original``.
      5. No fabricated links — every URL newly introduced in ``new`` must be in the
         evidence URL allow-list.
      6. No fabricated numbers — every numeric token newly introduced in ``new``
         must be traceable to the evidence text.
      7. Non-empty value — the edit must actually add a sourced link or a sourced
         number; an insertion that cites nothing is rejected as a no-op.
    """
    allowed_urls, evidence_blob = _evidence_index(evidence)
    allowed_numbers = _numbers(evidence_blob)

    accepted: list[HardenEdit] = []
    rejected: list[dict] = []
    seen_targets: set[str] = set()

    def _reject(original: str, new: str, reason: str) -> None:
        rejected.append({"original": original, "new": new, "reason": reason})

    for e in raw_edits[: MAX_EDITS * 4]:  # cap work; we keep at most MAX_EDITS anyway
        original = (e.get("original") or "").strip()
        new = (e.get("new") or "").strip()

        # 1. shape
        if not original or not new:
            _reject(original, new, "empty original or new")
            continue
        if original == new:
            _reject(original, new, "no change")
            continue

        # 2. targets real copy
        if original not in body_text:
            _reject(original, new, "original text not found on the page (would not apply)")
            continue
        if original in seen_targets:
            _reject(original, new, "duplicate target paragraph")
            continue

        # 3. additive-only
        if original not in new:
            _reject(original, new, "not additive — original copy was altered or dropped")
            continue

        # 4. bounded growth
        grew = len(new) - len(original)
        if grew <= 0:
            _reject(original, new, "did not add content")
            continue
        if grew > MAX_GROWTH_CHARS:
            _reject(original, new, f"insertion too large ({grew} chars)")
            continue

        # 5. no fabricated links
        new_urls = {_norm_url(u) for u in _URL_RE.findall(new)}
        orig_urls = {_norm_url(u) for u in _URL_RE.findall(original)}
        introduced_urls = new_urls - orig_urls
        bad_url = next((u for u in introduced_urls if u not in allowed_urls), None)
        if bad_url:
            _reject(original, new, f"fabricated/unsourced link: {bad_url}")
            continue

        # 6. no fabricated numbers
        introduced_numbers = _numbers(new) - _numbers(original)
        bad_num = next((n for n in introduced_numbers if n not in allowed_numbers), None)
        if bad_num:
            _reject(original, new, f"unsourced statistic: {bad_num}")
            continue

        # 7. must actually add something sourced
        used_urls = sorted(introduced_urls & allowed_urls)
        if not used_urls and not introduced_numbers:
            _reject(original, new, "insertion cites no source (no link, no figure)")
            continue

        accepted.append(
            HardenEdit(
                original=original,
                new=new,
                evidence_urls=used_urls,
                summary=(e.get("summary") or "Add a sourced fact to existing copy").strip(),
            )
        )
        seen_targets.add(original)
        if len(accepted) >= MAX_EDITS:
            break

    return accepted, rejected


# --------------------------------------------------------------------------- #
# default LLM-backed steps (injectable; network only here)
# --------------------------------------------------------------------------- #
def _parse_json_array(text: str) -> list:
    """Pull the first JSON array out of an LLM response. [] on failure."""
    if not text:
        return []
    fenced = re.search(r"\[.*\]", text, re.DOTALL)
    raw = fenced.group(0) if fenced else text
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def research_evidence(topic: str, kind: str, *, run=None, ask_cited=None) -> list[Evidence]:
    """Fetch REAL, source-cited facts for ``topic`` via a web-grounded model.

    Uses Perplexity (which returns a ``citations`` allow-list of the URLs it
    actually used). An evidence item is kept only if its ``source_url`` belongs
    to a domain Perplexity cited — that domain check is the *first* grounding
    gate; ``validate_edits`` is the second, at insertion time.
    """
    if ask_cited is None:
        from apps.analyzer.pipeline.llm import ask_llm_with_citations

        ask_cited = ask_llm_with_citations

    want = {
        "statistic": "specific, recent statistics with the exact figure and the source",
        "citation": "authoritative sources (studies, standards bodies, official docs) worth citing",
        "quote": "verbatim quotes from named experts/officials, each with the source",
    }.get(kind, "authoritative facts with sources")

    prompt = (
        f"Find 3-5 {want} relevant to: {topic}.\n"
        "Only include facts you can attribute to a real, citable web source. "
        "Return ONLY a JSON array, each item: "
        '{"statement": "...", "value": "the figure or exact quote", '
        '"source_url": "https://...", "source_title": "..."}.\n'
        "Do not include any fact you cannot source. No commentary outside the JSON."
    )
    text, citations = ask_cited(
        prompt, preferred_provider="perplexity", max_tokens=1200, purpose=f"hardener.research.{kind}"
    )
    cited_domains = {_norm_url(c.get("url", "")).split("/")[0] for c in citations if c.get("url")}

    out: list[Evidence] = []
    for item in _parse_json_array(text):
        if not isinstance(item, dict):
            continue
        url = (item.get("source_url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        domain = _norm_url(url).split("/")[0]
        # Grounding gate: keep only facts whose source domain Perplexity actually cited.
        if cited_domains and domain not in cited_domains:
            continue
        out.append(
            Evidence(
                kind=kind,
                statement=(item.get("statement") or "").strip(),
                value=(item.get("value") or "").strip(),
                source_url=url,
                source_title=(item.get("source_title") or "").strip(),
            )
        )
    return out


def propose_edits(body_text: str, evidence: list[Evidence], kind: str, *, run=None, ask=None) -> list[dict]:
    """Ask a model to weave the evidence into existing paragraphs, ADDITIVELY.

    Returns raw ``{original, new}`` dicts — unvalidated. ``validate_edits`` is the
    gate; this only has to produce candidates.
    """
    if ask is None:
        from apps.analyzer.pipeline.llm import ask_llm

        ask = ask_llm

    body = body_text[:8000]
    ev_lines = "\n".join(f'- {e.statement} (value: "{e.value}") [SOURCE: {e.source_url}]' for e in evidence)
    label = {"statistic": "statistics", "citation": "citations to the sources", "quote": "expert quotes"}.get(
        kind, "sourced facts"
    )
    prompt = (
        f"Below is the visible text of a web page, then a list of REAL sourced facts.\n"
        f"Improve the page's AI visibility by weaving {label} into it.\n\n"
        "STRICT RULES:\n"
        "- Pick 1-3 short EXISTING paragraphs from the page. For each, return the paragraph "
        "VERBATIM as `original`, and a `new` version that is the SAME text with the sourced fact "
        "ADDED (a sentence appended, or an inline citation). The original wording must remain "
        "inside `new` unchanged — only ADD.\n"
        "- Use ONLY the figures, quotes, and URLs from the facts list. Never invent a number, "
        "quote, or link. Attribute each addition (e.g. 'according to <source>' with the URL).\n"
        "- Return ONLY a JSON array: "
        '[{"original": "...", "new": "...", "summary": "..."}].\n\n'
        f"PAGE TEXT:\n{body}\n\n"
        f"SOURCED FACTS:\n{ev_lines}\n"
    )
    text = ask(prompt, preferred_provider="gpt", max_tokens=2000, purpose=f"hardener.propose.{kind}")
    raw = _parse_json_array(text)
    return [r for r in raw if isinstance(r, dict)]


# --------------------------------------------------------------------------- #
# orchestrator — research → propose → validate
# --------------------------------------------------------------------------- #
def harden(
    *,
    run,
    finding_code: str,
    page_url: str,
    body_text: str,
    research=research_evidence,
    propose=propose_edits,
) -> HardenProposal:
    """Produce grounded, validated hardening edits for one content finding.

    ``research`` and ``propose`` are injectable so this is fully testable offline.
    Returns a HardenProposal; ``.ok`` is False (with ``.note``) when there's
    nothing safe to apply — the caller then keeps the finding manual, with the
    note as the reason.
    """
    proposal = HardenProposal(finding_code=finding_code, page_url=page_url)

    kind = KIND_BY_FINDING.get(finding_code)
    if not kind:
        proposal.note = f"{finding_code} is not a content-hardening finding."
        return proposal

    if not body_text or len(body_text.strip()) < MIN_BODY_CHARS:
        proposal.note = "Not enough page text to harden."
        return proposal

    brand = getattr(run, "brand_name", "") or ""
    topic = f"{brand} — {page_url}".strip(" —") or page_url
    try:
        evidence = research(topic, kind, run=run)
    except Exception as exc:  # noqa: BLE001 — research is best-effort
        logger.warning("hardener research failed for %s: %s", finding_code, exc)
        evidence = []
    proposal.evidence = evidence
    if len(evidence) < MIN_EVIDENCE:
        proposal.note = "No citable sources found for this page — kept manual to avoid guessing."
        return proposal

    try:
        raw = propose(body_text, evidence, kind, run=run)
    except Exception as exc:  # noqa: BLE001
        logger.warning("hardener propose failed for %s: %s", finding_code, exc)
        raw = []

    edits, rejected = validate_edits(raw, evidence, body_text)
    proposal.edits = edits
    proposal.rejected = rejected
    if not edits:
        proposal.note = "Found sources but couldn't add them without altering existing copy — kept manual."
    return proposal
