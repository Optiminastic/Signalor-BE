"""Gather every observed signal a run has, for grounding task discovery.

``site_findings`` used to read four sources: the crawl, the analyzer's pillar
checks, the SiteOne crawl and a GA4/GSC bundle. Meanwhile the run had already
measured far more and thrown none of it at the problem — which engines answered
which prompt and who they cited instead, whether any AI crawler has ever fetched
the pricing page, which tracked prompts have no answering page at all.

That is why the generic rules still looked competitive: the dynamic engine was
reasoning from a fraction of what the product knows.

Every collector here is independently fail-soft and returns ``None`` when its
source has nothing. ``None`` matters: the prompt states an absent source as
absent, so the model never reads "no data" as "zero" — the same rule the crawler
and coverage reports follow.

Pure DB reads, no LLM calls, no network. Cheap enough to run on every analysis.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("apps")

# Per-source caps. The prompt has a finite budget and one verbose source must not
# crowd out five useful ones.
MAX_PROMPT_ROWS = 12
MAX_CITED_DOMAINS = 10
MAX_COMPETITORS = 8
MAX_COVERAGE_ROWS = 10
MAX_GAP_ROWS = 8
MAX_UNCRAWLED = 8


def _safe(label: str):
    """Decorator: a collector that fails returns None instead of killing the set."""

    def wrap(fn):
        def run(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                logger.warning("task_signals: %s collector failed", label, exc_info=True)
                return None

        run.__name__ = fn.__name__
        run.__doc__ = fn.__doc__
        return run

    return wrap


@_safe("prompt_citations")
def collect_prompt_citations(run) -> dict | None:
    """Which tracked prompts the brand lost, and who got cited instead.

    The strongest signal the product has, because it is *observed*: these are the
    exact answers engines gave and the exact sources they used. A task built on
    this can name the prompt, the engines and the competitor.
    """
    from apps.analyzer.models import PromptTrack

    tracks = (
        PromptTrack.objects.filter(analysis_run=run, deleted_at__isnull=True)
        .prefetch_related("results__citations")
        .order_by("-score")[:MAX_PROMPT_ROWS]
    )
    rows: list[dict] = []
    cited: dict[str, int] = {}
    for track in tracks:
        results = list(track.results.all())
        if not results:
            continue  # never fired — unknown, not lost
        engines_total = len(results)
        engines_hit = sum(1 for r in results if r.brand_mentioned)
        row = {
            "prompt": (track.prompt_text or "")[:160],
            "type": track.prompt_type or "",
            "engines_mentioning": engines_hit,
            "engines_asked": engines_total,
        }
        if engines_hit == 0:
            # Who answered instead — the actionable half.
            others = []
            for result in results:
                for c in result.citations.all():
                    if c.is_brand:
                        continue
                    d = (c.domain or "").lower().removeprefix("www.")
                    if d and d not in others:
                        others.append(d)
                    cited[d] = cited.get(d, 0) + 1
            row["cited_instead"] = others[:5]
        rows.append(row)

    if not rows:
        return None
    lost = [r for r in rows if r["engines_mentioning"] == 0]
    return {
        "prompts": rows,
        "lost_count": len(lost),
        "tracked_count": len(rows),
        "top_cited_instead": sorted(cited.items(), key=lambda kv: -kv[1])[:MAX_CITED_DOMAINS],
    }


@_safe("competitors")
def collect_competitors(run) -> list[dict] | None:
    """Discovered competitors and how their pages score against this brand's."""
    from apps.analyzer.models import Competitor

    rows = [
        {
            "name": c.name,
            "url": c.url,
            "score": round(float(c.composite_score or 0), 1),
            "tier": c.tier or "",
        }
        for c in Competitor.objects.filter(analysis_run=run).order_by("-composite_score")[
            :MAX_COMPETITORS
        ]
    ]
    return rows or None


@_safe("crawler_telemetry")
def collect_crawler_telemetry(run) -> dict | None:
    """Which AI crawlers actually fetched the site, and which pages they missed.

    Observed, not inferred. "GPTBot has never fetched /pricing" is a fact; the
    absence of telemetry is *not* the same as absence of crawling, so this returns
    None rather than a misleading zero when nothing is instrumented.
    """
    from apps.analyzer.services.crawler_access import report_for_run

    report = report_for_run(run)
    if not report or not report.get("has_telemetry"):
        return None
    summary = report.get("summary") or {}
    return {
        "blocked_engines": summary.get("blocked_engines") or [],
        "engines": [
            {"engine": e.get("engine"), "status": e.get("status"), "hits": e.get("hits")}
            for e in (report.get("engines") or [])[:10]
        ],
        "uncrawled_pages": (report.get("uncrawled_pages") or [])[:MAX_UNCRAWLED],
    }


@_safe("prompt_coverage")
def collect_prompt_coverage(run) -> dict | None:
    """Tracked prompts with no page on the site that answers them.

    Distinguishes "needs a new page" from "needs an existing page improved",
    which is the difference between two completely different tasks.
    """
    from apps.analyzer.services.prompt_coverage import report_for_run

    report = report_for_run(run)
    summary = (report or {}).get("summary") or {}
    # An unindexed corpus reports unknown; that is not "uncovered" and must not
    # become a "write these pages" task.
    if not summary or summary.get("coverage_pct") is None:
        return None
    return {
        "covered": summary.get("covered"),
        "measurable": summary.get("measurable"),
        "needs_page": (summary.get("needs_page") or [])[:MAX_COVERAGE_ROWS],
        "needs_section": (summary.get("needs_section") or [])[:MAX_COVERAGE_ROWS],
    }


@_safe("citation_gaps")
def collect_citation_gaps(run) -> list[dict] | None:
    """Domains cited on prompts the brand lost — the outreach queue.

    ``verify=False``: verification costs one search per domain and this is a
    grounding read, not the outreach view.
    """
    from apps.analyzer.services.citation_gaps import report_for_run

    report = report_for_run(run, verify=False)
    targets = (report or {}).get("targets") or []
    rows = [
        {
            "domain": t.get("domain"),
            "prompts_won": t.get("prompts_won"),
            "status": t.get("status"),
        }
        for t in targets[:MAX_GAP_ROWS]
    ]
    return rows or None


@_safe("domain_authority")
def collect_domain_authority(run) -> dict | None:
    """Domain rating and backlink profile, when a provider is configured.

    ``source`` is None when neither Ahrefs nor OpenPageRank is available, and an
    absent rating must not read as a rating of zero.
    """
    from urllib.parse import urlparse

    from apps.analyzer.services.domain_authority import get_for_domain

    host = urlparse(run.url or "").netloc.removeprefix("www.")
    if not host:
        return None
    data = get_for_domain(host)
    if not data or not data.get("source"):
        return None
    return {
        "domain_rating": data.get("domain_rating"),
        "backlinks": data.get("backlinks"),
        "linking_websites": data.get("linking_websites"),
        "source": data.get("source"),
    }


@_safe("brand_profile")
def collect_brand_profile(run) -> dict | None:
    """Approved brand facts, so findings argue from verified positioning."""
    from apps.organizations.models import BrandProfile

    org = getattr(run, "organization", None)
    if org is None:
        return None
    profile = BrandProfile.objects.filter(organization=org).first()
    if profile is None:
        return None
    return {
        "status": profile.status,
        "identity": profile.identity or {},
        "positioning": profile.positioning or {},
        "audience": profile.audience or {},
        "canonical_facts": profile.canonical_facts or {},
    }


def collect_all(run) -> dict:
    """Every signal available for this run, keyed by source name.

    Keys whose source had nothing are present with a ``None`` value on purpose —
    the caller renders "not measured" rather than dropping the section, so the
    model cannot mistake a missing integration for a zero score.
    """
    return {
        "prompt_citations": collect_prompt_citations(run),
        "competitors": collect_competitors(run),
        "crawler_telemetry": collect_crawler_telemetry(run),
        "prompt_coverage": collect_prompt_coverage(run),
        "citation_gaps": collect_citation_gaps(run),
        "domain_authority": collect_domain_authority(run),
        "brand_profile": collect_brand_profile(run),
    }
