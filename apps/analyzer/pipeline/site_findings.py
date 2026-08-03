"""Open-ended site audit: findings discovered by reading the site, not matched
against a fixed rule list.

``pipeline/recommendations.py`` holds 83 hand-written rules. Each one is a real
deterministic check and they stay: measuring "this page has no h1" is better done
by a parser than by a model. But a rule engine can only ever report problems
somebody wrote a checker for, which is why every customer's task list looked like
the same SEO checklist.

This module is the other half. It reads the pages actually crawled for the run
and reports what is wrong with *this* site, including the class of issue no
generic rule can express - most notably pages of the same kind built
inconsistently (a guide page that lacks the structure its sibling blog posts
have).

**The anti-hallucination gate is the point of this module.** An LLM asked to
"find problems" will happily invent them. So every finding must carry a verbatim
quote from a crawled page, and ``_verify_evidence`` checks that quote against the
page text before the finding survives. Findings whose evidence cannot be located
are dropped and logged. This is the same contract used elsewhere in the pipeline:
a claim we cannot ground is discarded, never shown.
"""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger("apps")

# Cost and noise bounds. A run crawls up to 12 pages (see tasks/analysis.py), so
# MAX_PAGES at 14 covers the whole crawl plus headroom rather than an arbitrary
# first-8 slice — the module can only find what it is shown.
#
# Env-overridable on purpose: these are the only levers on this module's cost per
# run, and raising findings raises output tokens. Dial them back with
# SITE_FINDINGS_* without a deploy if spend spikes.
MAX_PAGES = int(os.getenv("SITE_FINDINGS_MAX_PAGES", "14"))
PAGE_EXCERPT_CHARS = int(os.getenv("SITE_FINDINGS_EXCERPT_CHARS", "2200"))
MAX_FINDINGS = int(os.getenv("SITE_FINDINGS_MAX_FINDINGS", "12"))

# Output budget must scale with the number of findings requested, or the model
# runs out of tokens mid-list and the tail is silently truncated.
_TOKENS_PER_FINDING = 420
_BASE_TOKENS = 600

# Shortest evidence quote we will trust. Anything below this matches by accident
# ("the", "Home") and would let an ungrounded finding through the gate.
MIN_EVIDENCE_CHARS = 25

VALID_PILLARS = {"content", "schema", "eeat", "technical"}
VALID_PRIORITIES = {"critical", "high", "medium"}

# What fixing a finding in each pillar actually buys, in the user's terms. Shown
# as the task's "why" so the Tasks list can explain what a task is for instead of
# just what it is.
PILLAR_RATIONALE = {
    "content": "Makes the page directly answerable, so AI engines can extract and quote it.",
    "schema": "Gives engines machine-readable facts about the page, which raises citation odds.",
    "eeat": "Strengthens the trust signals engines weigh before citing a source.",
    "technical": "Removes crawl and rendering barriers that stop engines reading the page at all.",
}

# Effort by severity. Deliberately coarse - these are planning hints, not
# estimates anyone should hold the user to.
PRIORITY_EFFORT = {
    "critical": {"difficulty": "medium", "minutes": 30, "xp": 80},
    "high": {"difficulty": "medium", "minutes": 25, "xp": 60},
    "medium": {"difficulty": "easy", "minutes": 15, "xp": 40},
}

# Namespace so a discovered finding can never collide with one of the 83 rule
# codes, and so downstream code can tell the two apart.
FINDING_PREFIX = "site:"


def _normalize(text: str) -> str:
    """Collapse whitespace and lowercase, for tolerant quote matching.

    The model reliably copies wording but not spacing: it will turn a newline
    inside a heading into a space, or collapse a double space. Matching on
    normalized text accepts that without accepting a paraphrase.
    """
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (title or "").lower()).strip("_")
    return slug[:60] or "finding"


def _page_corpus(crawls: list) -> dict[str, str]:
    """Map url -> normalized page text for every successfully crawled page."""
    corpus: dict[str, str] = {}
    for crawl in crawls:
        if not getattr(crawl, "ok", False):
            continue
        corpus[crawl.url] = _normalize(getattr(crawl, "text", "") or "")
    return corpus


def _verify_evidence(evidence: str, corpus: dict[str, str]) -> bool:
    """True when the quote really appears on one of the crawled pages.

    Checked against every page rather than only the one the model named, because
    quoting the right text off the wrong URL is a citation error, not a
    fabrication - the finding is still real and the URL is corrected separately.
    """
    quote = _normalize(evidence)
    if len(quote) < MIN_EVIDENCE_CHARS:
        return False
    return any(quote in page_text for page_text in corpus.values())


def _important_paths(signals: dict | None) -> set[str]:
    """Page paths GA4/Search Console say actually get traffic.

    Shapes differ per source (GA4 rows carry ``path``, GSC rows ``page``), so
    this reads whichever key is present rather than assuming one.
    """
    if not signals:
        return set()
    paths: set[str] = set()
    for source in ("ga", "gsc"):
        block = signals.get(source) or {}
        for row in block.get("top_pages") or []:
            if not isinstance(row, dict):
                continue
            value = row.get("path") or row.get("page") or row.get("url") or ""
            if isinstance(value, str) and value.strip():
                paths.add(value.strip().rstrip("/"))
    return paths


def _rank_crawls(crawls: list, signals: dict | None) -> list:
    """Most-worth-reading pages first.

    Previously this took ``crawls[:MAX_PAGES]`` — whatever the crawler happened
    to fetch first. Ordering matters twice: it decides which pages survive the
    cap, and the model attends more closely to what it reads first. Homepage
    leads, then anything GA4/GSC report traffic for, then the longest pages
    (more text is more surface for a real finding).
    """
    important = _important_paths(signals)

    def rank(index_and_crawl: tuple[int, object]) -> tuple[int, int, int]:
        index, crawl = index_and_crawl
        url = getattr(crawl, "url", "") or ""
        path = "/" + url.split("//", 1)[-1].split("/", 1)[-1] if "//" in url else url
        path = path.rstrip("/")
        is_home = 0 if index == 0 else 1
        is_important = 0 if (path in important or url.rstrip("/") in important) else 1
        return (is_home, is_important, -len(getattr(crawl, "text", "") or ""))

    return [crawl for _, crawl in sorted(enumerate(crawls), key=rank)]


def _build_pages_block(crawls: list, signals: dict | None = None) -> str:
    """Render each page as a capped excerpt, marking where we cut it.

    The marker matters: without it a model reads an excerpt that stops
    mid-sentence and reports "this page's content is truncated" as a defect on
    the customer's site, when the truncation is ours. Observed in testing.
    """
    parts = []
    for crawl in _rank_crawls(crawls, signals)[:MAX_PAGES]:
        if not getattr(crawl, "ok", False):
            continue
        text = re.sub(r"\s+", " ", (getattr(crawl, "text", "") or "")).strip()
        if not text:
            continue
        excerpt = text[:PAGE_EXCERPT_CHARS]
        if len(text) > PAGE_EXCERPT_CHARS:
            excerpt += (
                f"\n[EXCERPT ENDS HERE - this page is {len(text)} characters long and was cut "
                f"at {PAGE_EXCERPT_CHARS} for this review. The page itself is NOT truncated.]"
            )
        parts.append(f"--- PAGE: {crawl.url} ---\n{excerpt}")
    return "\n\n".join(parts)


def _already_found_block(existing_recs: list[dict]) -> str:
    titles = sorted({(r.get("title") or "").strip() for r in existing_recs if r.get("title")})
    return "\n".join(f"- {t}" for t in titles) or "- (nothing yet)"


def _json_block(value, limit: int) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, default=str)[:limit]
    except (TypeError, ValueError):
        return ""


def _pillar_block(pillar_details: dict | None) -> str:
    """The analyzer's own measured checks, per pillar.

    These are facts the model must reason from rather than re-derive: it can see
    that ``word_count`` is 180 or that ``has_author`` is false without guessing.
    """
    if not pillar_details:
        return "- analyzer pillar checks: not available"
    lines = []
    for pillar, details in pillar_details.items():
        if not isinstance(details, dict):
            continue
        checks = {
            k: v
            for k, v in (details.get("checks") or {}).items()
            # Nested sub-detail dicts are too big for the prompt; keep the scalars.
            if isinstance(v, (int, float, bool, str))
        }
        lines.append(
            f"- {pillar}: score={details.get('score')} "
            f"findings={_json_block(details.get('findings') or [], 300)} "
            f"checks={_json_block(checks, 700)}"
        )
    return "\n".join(lines) or "- analyzer pillar checks: not available"


def _siteone_block(siteone: dict | None) -> str:
    """SiteOne crawler output: real technical deductions with their own fixes."""
    if not siteone:
        return "- SiteOne technical crawl: not available for this run"
    cats = [
        f"{c.get('name')}={c.get('score')}"
        for c in (siteone.get("categories") or [])
        if isinstance(c, dict)
    ]
    deductions = []
    for cat in (siteone.get("categories") or [])[:6]:
        for ded in (cat.get("deductions") or [])[:3]:
            deductions.append(f"{cat.get('name')}: {ded.get('reason')}")
    return (
        f"- overall={siteone.get('overall_score')} categories={', '.join(cats[:10])}\n"
        f"- severity counts: {_json_block(siteone.get('severity_counts') or {}, 300)}\n"
        f"- deductions: {_json_block(deductions[:12], 900)}"
    )


def _analytics_block(signals: dict | None) -> str:
    """GA4 + Search Console, or an explicit statement that they are not connected.

    Saying "not connected" matters: an absent source must never be read as zero
    traffic or zero impressions, which would invent a finding out of missing data.
    """
    if not signals:
        return "- Google Analytics: not connected\n- Search Console: not connected"

    flags = signals.get("flags") or {}
    parts = []

    ga = signals.get("ga")
    if flags.get("has_ga") and ga:
        parts.append(
            f"- Google Analytics (GA4) {ga.get('date_start')} to {ga.get('date_end')}: "
            f"sessions={ga.get('sessions')} organic={ga.get('organic_sessions')} "
            f"({ga.get('organic_pct')}%) bounce={ga.get('bounce_rate')} "
            f"trend={ga.get('sessions_trend')}\n"
            f"  top pages by sessions: {_json_block(ga.get('top_pages') or [], 500)}\n"
            f"  top sources: {_json_block(ga.get('top_sources') or [], 300)}"
        )
    else:
        parts.append("- Google Analytics: not connected (do not infer traffic numbers)")

    gsc = signals.get("gsc")
    if flags.get("has_gsc") and gsc:
        parts.append(
            f"- Search Console: clicks={gsc.get('clicks')} impressions={gsc.get('impressions')} "
            f"ctr={gsc.get('ctr')} avg_position={gsc.get('position')} "
            f"trend={gsc.get('clicks_trend')} "
            f"indexed={gsc.get('indexed_count')} not_indexed={gsc.get('not_indexed_count')}\n"
            f"  queries the site already ranks for: {_json_block(gsc.get('top_queries') or [], 900)}\n"
            f"  top pages by clicks: {_json_block(gsc.get('top_pages') or [], 400)}"
        )
    else:
        parts.append("- Search Console: not connected (do not infer queries or rankings)")

    return "\n".join(parts)


def _ai_visibility_block(ai_visibility: dict | None) -> str:
    """How AI engines currently answer about this brand."""
    if not ai_visibility:
        return "- AI engine visibility: not available for this run"
    checks = ai_visibility.get("checks") or {}
    return (
        f"- score={ai_visibility.get('score')} "
        f"findings={_json_block(ai_visibility.get('findings') or [], 300)}\n"
        f"- detail: {_json_block({k: v for k, v in checks.items() if not isinstance(v, dict)}, 700)}"
    )


def _citations_block(data: dict | None) -> str:
    """What engines actually answered, and who they cited instead.

    The most actionable block here because it is observed rather than inferred: a
    finding built on it can name the prompt, the engines and the competitor that
    took the citation.
    """
    if not data:
        return "- Prompt tracking: not measured for this run (no prompts have fired yet)"
    lines = [
        f"- {data['lost_count']} of {data['tracked_count']} tracked prompts got NO brand mention",
    ]
    if data.get("top_cited_instead"):
        cited = ", ".join(f"{d} ({n}x)" for d, n in data["top_cited_instead"])
        lines.append(f"- cited instead of this brand: {cited}")
    for row in data.get("prompts") or []:
        mark = "LOST" if row["engines_mentioning"] == 0 else "ok"
        detail = f"  [{mark}] \"{row['prompt']}\" — {row['engines_mentioning']}/{row['engines_asked']} engines"
        if row.get("cited_instead"):
            detail += f"; they cited {', '.join(row['cited_instead'])}"
        lines.append(detail)
    return "\n".join(lines)


def _competitors_block(rows: list | None) -> str:
    if not rows:
        return "- Competitors: none discovered for this run"
    return "\n".join(
        f"- {r['name']} ({r['url']}) score={r['score']}" + (f" tier={r['tier']}" if r.get("tier") else "")
        for r in rows
    )


def _crawler_block(data: dict | None) -> str:
    """Observed bot activity. Absent telemetry is stated, never read as zero."""
    if not data:
        return (
            "- AI crawler telemetry: not instrumented, so whether engines fetch this "
            "site is UNKNOWN (do not claim they have not visited)"
        )
    lines = []
    if data.get("blocked_engines"):
        lines.append(f"- BLOCKED by robots.txt: {', '.join(data['blocked_engines'])}")
    for e in data.get("engines") or []:
        lines.append(f"- {e.get('engine')}: {e.get('status')} ({e.get('hits')} hits)")
    if data.get("uncrawled_pages"):
        lines.append(f"- pages no AI crawler has fetched: {', '.join(data['uncrawled_pages'])}")
    return "\n".join(lines) or "- AI crawler telemetry: present but empty"


def _coverage_block(data: dict | None) -> str:
    """Which prompts have no answering page — 'write one' vs 'improve one'."""
    if not data:
        return "- Prompt→page coverage: not measured (corpus not indexed yet)"
    lines = [f"- {data.get('covered')} of {data.get('measurable')} prompts are answered by a page"]
    if data.get("needs_page"):
        lines.append(f"- NO page answers these: {_json_block(data['needs_page'], 600)}")
    if data.get("needs_section"):
        lines.append(f"- a page exists but answers weakly: {_json_block(data['needs_section'], 600)}")
    return "\n".join(lines)


def _gaps_block(rows: list | None) -> str:
    if not rows:
        return "- Citation gaps: none recorded"
    return "\n".join(
        f"- {r['domain']} wins {r['prompts_won']} of your prompts (status: {r.get('status')})"
        for r in rows
    )


def _authority_block(data: dict | None) -> str:
    if not data:
        return "- Domain authority: no provider configured, so authority is UNKNOWN"
    return (
        f"- domain_rating={data.get('domain_rating')} backlinks={data.get('backlinks')} "
        f"linking_websites={data.get('linking_websites')} (via {data.get('source')})"
    )


def _brand_profile_block(data: dict | None) -> str:
    """Verified brand facts, so a finding argues from approved positioning."""
    if not data:
        return "- Brand profile: not created yet"
    return (
        f"- status={data.get('status')}\n"
        f"- identity: {_json_block(data.get('identity'), 500)}\n"
        f"- positioning: {_json_block(data.get('positioning'), 500)}\n"
        f"- audience: {_json_block(data.get('audience'), 400)}\n"
        f"- verified facts: {_json_block(data.get('canonical_facts'), 500)}"
    )


def _to_recommendation(finding, corpus: dict[str, str]) -> dict:
    """Convert a validated SiteFinding into the rec dict the pipeline persists."""
    pillar = finding.pillar if finding.pillar in VALID_PILLARS else "content"
    priority = finding.priority if finding.priority in VALID_PRIORITIES else "medium"
    url = finding.url if finding.url in corpus else next(iter(corpus), "")

    return {
        "finding_code": f"{FINDING_PREFIX}{_slugify(finding.title)}",
        "pillar": pillar,
        "priority": priority,
        "title": finding.title[:255],
        "description": finding.issue[:600],
        "action": finding.fix[:1200],
        "category": pillar,
        # Every task must be able to answer "why am I doing this?". Rule-based
        # findings and GEO signals already populate ``why``; discovered findings
        # were shipping it empty, which is what left the Tasks table unable to
        # show what a task is actually for.
        "why": PILLAR_RATIONALE.get(pillar, PILLAR_RATIONALE["content"]),
        # Effort estimates so a discovered task can be ranked and planned next to
        # a rule-based one instead of rendering as a blank cell.
        "difficulty": PRIORITY_EFFORT[priority]["difficulty"],
        "estimated_minutes": PRIORITY_EFFORT[priority]["minutes"],
        "xp_reward": PRIORITY_EFFORT[priority]["xp"],
        # Marks these as discovered rather than rule-matched. It also keeps them
        # out of ``overview_signals``, which reads source="analyzer" — otherwise
        # a discovered finding would feed back into the prompt that produced it.
        "source": "ai_insight",
        "affected_pages": [url] if url else [],
        "evidence": {"quote": finding.evidence[:500], "url": url},
        # Already page-specific, so the enrichment pass has nothing to add.
        "generated_content": {
            "type": "guidance",
            "data": {
                "observation": finding.evidence[:500],
                "steps": [s for s in [finding.fix[:1200]] if s],
                "snippet": finding.snippet[:2000],
            },
            "source": "site_findings",
        },
    }


def discover_site_findings(
    crawls: list,
    brand: str,
    homepage_url: str,
    existing_recs: list[dict] | None = None,
    *,
    pillar_details: dict | None = None,
    siteone: dict | None = None,
    signals: dict | None = None,
    ai_visibility: dict | None = None,
    run_signals: dict | None = None,
    limit: int = MAX_FINDINGS,
) -> list[dict]:
    """Read every available signal for this site and return specific findings.

    Sources, all optional and independently fail-soft:
      ``crawls``         - page text from the analyzer's own crawl (required)
      ``pillar_details`` - the analyzer's measured content/schema/eeat/technical checks
      ``siteone``        - SiteOne technical crawl deductions
      ``signals``        - GA4 + Search Console bundle (``build_overview_signals``)
      ``ai_visibility``  - how AI engines currently answer about the brand
      ``run_signals``    - everything else the run measured, from
                           ``services.task_signals.collect_all``: which prompts
                           were lost and who was cited instead, discovered
                           competitors, real AI-crawler telemetry, prompt-to-page
                           coverage, citation gaps, domain authority and the
                           approved brand profile

    Sources that are absent are stated as absent in the prompt rather than
    omitted, so the model never reads "no data" as "zero".

    Returns rec-shaped dicts ready to merge with the rule engine's output. Empty
    list on any failure - this augments the deterministic rules, it never gates
    them, so a bad LLM day costs extra findings and nothing else.
    """
    from apps.analyzer.prompts import render
    from core.llm.structured import ask_structured_list

    from .schemas import SiteFinding

    corpus = _page_corpus(crawls)
    if not corpus:
        logger.info("site_findings: no crawled page text for %s; skipping", homepage_url)
        return []

    pages_block = _build_pages_block(crawls, signals)
    if not pages_block:
        return []

    sig = run_signals or {}
    try:
        prompt = render(
            "site_findings",
            brand=brand or "the website",
            url=homepage_url,
            pages_block=pages_block,
            already_found=_already_found_block(existing_recs or []),
            analyzer_block=_pillar_block(pillar_details),
            siteone_block=_siteone_block(siteone),
            analytics_block=_analytics_block(signals),
            ai_visibility_block=_ai_visibility_block(ai_visibility),
            citations_block=_citations_block(sig.get("prompt_citations")),
            competitors_block=_competitors_block(sig.get("competitors")),
            crawler_block=_crawler_block(sig.get("crawler_telemetry")),
            coverage_block=_coverage_block(sig.get("prompt_coverage")),
            gaps_block=_gaps_block(sig.get("citation_gaps")),
            authority_block=_authority_block(sig.get("domain_authority")),
            brand_profile_block=_brand_profile_block(sig.get("brand_profile")),
            count=limit,
        )
    except Exception:
        logger.exception("site_findings: prompt render failed for %s", homepage_url)
        return []

    findings = ask_structured_list(
        prompt,
        SiteFinding,
        tier="strong",
        # Scales with the requested count so a larger list can't be truncated
        # mid-item, which would lose the tail silently.
        max_tokens=_BASE_TOKENS + _TOKENS_PER_FINDING * max(limit, 1),
        purpose="site-findings",
    )
    if not findings:
        logger.info("site_findings: model returned nothing for %s", homepage_url)
        return []

    kept: list[dict] = []
    dropped: list[str] = []
    seen_codes: set[str] = set()
    for finding in findings:
        if not _verify_evidence(finding.evidence, corpus):
            dropped.append(finding.title)
            continue
        rec = _to_recommendation(finding, corpus)
        if rec["finding_code"] in seen_codes:
            continue
        seen_codes.add(rec["finding_code"])
        kept.append(rec)

    if dropped:
        # Loud on purpose: a high drop rate means the prompt or the model is
        # drifting toward invention, and that is worth seeing in the logs.
        logger.warning(
            "site_findings: dropped %d/%d ungrounded findings for %s: %s",
            len(dropped),
            len(findings),
            homepage_url,
            dropped[:5],
        )
    logger.info(
        "site_findings: kept %d site-specific findings for %s", len(kept), homepage_url
    )
    return kept[:limit]
