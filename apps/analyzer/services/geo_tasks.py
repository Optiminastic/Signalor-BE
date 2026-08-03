"""GEO-signal task generator.

The static finding engine only sees on-page HTML. It is blind to what the
platform actually measures about generative-engine visibility: which tracked
prompts the brand is *not* cited for, which competitors win those prompts, and
which citation domains recur without the brand. This module turns that measured
signal into high-value, grounded tasks.

Each task's impact is a real observed statistic (e.g. "cited in 0/5 tracked
prompts; a competitor is cited in 4"), never a fabricated point value. Tasks are
persisted as ``Recommendation`` rows with ``source = geo_signal`` and refreshed
idempotently, mirroring ``services/overview_insights`` (source=ai_insight).

Graceful degradation: if no prompt-tracking results exist yet (e.g. the first
run, before prompts have fired), the generator returns ``[]`` - no error.
"""

from __future__ import annotations

import logging

from apps.analyzer.pipeline.offpage_presence import brand_present_on_domain

logger = logging.getLogger("apps")

# Synthetic, stable finding codes so verify/reprioritize routing and dedupe work.
CODE_PROMPT_LOST = "geo_prompt_lost"
CODE_COMPETITOR_CITED = "geo_competitor_cited"
CODE_CITATION_GAP = "geo_citation_gap"
CODE_COMPETITOR_PILLAR_GAP = "geo_competitor_pillar_gap"

# How many tasks to emit per category, highest-signal first.
# Lost prompts are the most directly actionable signal the platform has - each
# one names a real query a buyer asked and the brand did not answer - so this
# runs deeper than the other categories.
_MAX_LOST_PROMPTS = 6
_MAX_CITATION_GAPS = 2

# Open content / social / Q&A platforms are NOT actionable "get a placement here"
# targets — any established brand is already present (Vercel is all over Medium and
# YouTube), and "get mentioned on medium.com" is a vague non-task. A citation-gap
# task only makes sense for a discrete third-party authority (a publication, a
# directory, a docs/review site) the brand could realistically be added to. These
# are excluded from the citation-gap generator.
_PLATFORM_DOMAINS = frozenset({
    "medium.com", "youtube.com", "youtu.be", "reddit.com", "github.com",
    "gitlab.com", "stackoverflow.com", "stackexchange.com", "quora.com",
    "dev.to", "hashnode.com", "substack.com", "twitter.com", "x.com",
    "facebook.com", "linkedin.com", "instagram.com", "tiktok.com",
    "pinterest.com", "threads.net", "wikipedia.org", "wikimedia.org",
    "wordpress.com", "blogspot.com", "tumblr.com", "news.ycombinator.com",
})


def _is_platform_domain(domain: str) -> bool:
    """True for open content/social platforms that aren't a discrete placement target."""
    d = (domain or "").lower().lstrip(".")
    return any(d == p or d.endswith("." + p) for p in _PLATFORM_DOMAINS)


def _base(finding_code: str, pillar: str, priority: str) -> dict:
    """Common Recommendation kwargs for a GEO task."""
    from apps.analyzer.models import Recommendation

    return {
        "pillar": pillar,
        "priority": priority,
        "category": pillar,
        "finding_code": finding_code,
        "finding_key": finding_code,
        "source": Recommendation.Source.GEO_SIGNAL,
        "why": "AI engines cite trusted, well-represented brands - measured directly from live prompts.",
        "steps": [],
        "affected_pages": [],
    }


# ── Detailed, real "how to do it" step guides for the manual GEO tasks ──────────
# These are off-site / strategic tasks the code agent cannot auto-fix, so the value
# is a concrete playbook. Shape matches STEP_META in pipeline/recommendations.py:
# a list of {n, title, detail, xp}. Kept as data (no LLM) so steps are accurate.


def _citation_gap_steps(domain: str, count: int) -> list[dict]:
    """Outreach playbook to earn a legitimate mention/placement on ``domain``."""
    return [
        {"n": 1, "title": f"Decide the realistic placement type on {domain}",
         "detail": (
             f"Open {domain} and look at how it presents brands like yours. Pick the path that "
             f"fits: a directory/tool listing, a guest contribution, a product review, or an "
             f"editorial mention. Look for a 'Submit', 'Write for us', 'Add your tool', or "
             f"'Contact' link, or the category page your brand belongs on."), "xp": 5},
        {"n": 2, "title": "Find the right contact or submission route",
         "detail": (
             "Locate the exact submission form, or the editor/author who writes the pieces AI "
             "cites. Get a real person's email (Hunter.io, the site's LinkedIn, or the article "
             "byline) rather than a generic inbox - personalized outreach converts far better."), "xp": 10},
        {"n": 3, "title": "Prepare a specific, valuable pitch",
         "detail": (
             "Write a short pitch: who you are, the concrete value you add (a data-backed guest "
             "post idea, a tool listing with the right category, or why your product merits a "
             "review), and 1-2 proof points (customers, traffic, unique data). Avoid generic "
             "'please feature us' asks."), "xp": 10},
        {"n": 4, "title": "Submit or send it",
         "detail": (
             f"Send the personalized pitch or complete {domain}'s official submission. For a "
             f"listing, fill the full profile - logo, a description using your target terms, the "
             f"right category, and a link to your site. For editorial, attach an outline or draft."), "xp": 10},
        {"n": 5, "title": "Follow up and deliver",
         "detail": (
             "Follow up once after ~5-7 business days if there's no reply. When accepted, deliver "
             "promptly and confirm the published page includes your brand name and a link to your "
             "site."), "xp": 10},
        {"n": 6, "title": "Verify it's live and re-check visibility",
         "detail": (
             f"Confirm the page is public and indexable (not behind a login). AI engines re-crawl "
             f"{domain} regularly, so re-run this prompt's visibility check in a few weeks - you "
             f"should start being cited once the mention is indexed."), "xp": 15},
    ]


def _prompt_lost_steps(prompt: str, engine_list: str) -> list[dict]:
    """Playbook to win a tracked query the brand is not cited for."""
    return [
        {"n": 1, "title": "Pin down the intent behind the query",
         "detail": (
             f"Re-read the exact prompt and list what someone asking it on {engine_list} really "
             f"wants - a definition, a comparison, a how-to, or a recommendation. AI cites "
             f"whatever answers that intent most directly and completely."), "xp": 10},
        {"n": 2, "title": "Choose or create the page that should own it",
         "detail": (
             "Pick one existing page to strengthen, or create a dedicated page whose sole focus is "
             "answering this query. One focused page beats a paragraph buried in a general page."), "xp": 10},
        {"n": 3, "title": "Lead with a direct answer",
         "detail": (
             "Put a self-contained 2-3 sentence answer to the exact query in the first paragraph, "
             "before any preamble. AI engines extract the opening lines as the answer."), "xp": 15},
        {"n": 4, "title": "Add extractable structure and sources",
         "detail": (
             "Add an FAQ using the query and close variants as questions, a comparison table if "
             "relevant, bullet lists, and clear H2/H3 headings. Cite 2-3 authoritative sources "
             "inline to back your claims."), "xp": 15},
        {"n": 5, "title": "Mark it up with schema",
         "detail": (
             "Add FAQPage and/or Article JSON-LD so engines can parse the Q&A and metadata. The "
             "schema tasks in your list can be applied automatically with Fix with AI."), "xp": 10},
        {"n": 6, "title": "Publish, index, and re-check",
         "detail": (
             "Publish, submit the URL in Google Search Console to request indexing, then re-run "
             "this prompt's tracking in 1-2 weeks to confirm you are now cited."), "xp": 15},
    ]


def _competitor_cited_steps(names: str) -> list[dict]:
    """Playbook to close the citation gap with competitors AI recommends."""
    return [
        {"n": 1, "title": "See exactly where they win",
         "detail": (
             f"Open the prompts where you're absent and note which competitor pages AI cites "
             f"(e.g. {names}). Open those exact pages."), "xp": 5},
        {"n": 2, "title": "Reverse-engineer why they're cited",
         "detail": (
             "For each, list what they have that you don't: depth, original data/statistics, "
             "comparison tables, reviews, schema, and third-party mentions. Turn it into a short "
             "gap list."), "xp": 10},
        {"n": 3, "title": "Upgrade your equivalent page",
         "detail": (
             "On your matching page, close each gap - add the missing depth, a comparison that "
             "includes your brand, real data, an FAQ, and schema - so it becomes the most complete "
             "answer for that query."), "xp": 15},
        {"n": 4, "title": "Earn the same third-party signals",
         "detail": (
             "Pursue mentions/listings on the same high-authority sources that cite the "
             "competitor (see your 'Get mentioned on…' tasks). Shared citation sources are the "
             "fastest lever."), "xp": 15},
        {"n": 5, "title": "Re-analyze and track",
         "detail": (
             "Re-run analysis and re-check the affected prompts after ~2 weeks to confirm you're "
             "gaining citations against them."), "xp": 10},
    ]


def _competitor_pillar_gap_steps(competitor_name: str) -> list[dict]:
    """Playbook to close an AI-readiness score gap with a competitor."""
    return [
        {"n": 1, "title": "See where the gap is widest",
         "detail": (
             f"Open Competitors → {competitor_name} and compare pillar scores (content, schema, "
             f"E-E-A-T, technical). Note the 2-3 pillars where they most out-score you."), "xp": 10},
        {"n": 2, "title": "Work the highest-gap pillar first",
         "detail": (
             "Filter your task list to that pillar and complete its fixes. Content depth, E-E-A-T "
             "signals, and schema usually move the score the most."), "xp": 15},
        {"n": 3, "title": "Use Fix with AI for the quick wins",
         "detail": (
             "Many on-page and schema fixes can be applied automatically from your task list - do "
             "those first for fast, reliable gains."), "xp": 10},
        {"n": 4, "title": "Re-analyze to confirm",
         "detail": (
             f"After completing the pillar's fixes, re-run analysis and confirm your composite "
             f"score rose and the gap to {competitor_name} narrowed, then move to the next pillar."), "xp": 15},
    ]


def generate_geo_signal_tasks(run, industry: str = "default") -> list[dict]:
    """Build GEO-signal recommendation dicts from a run's measured prompt data.

    Returns a list of ``Recommendation`` kwargs (no DB writes). Empty when the run
    has no prompt-tracking results yet.
    """
    from apps.analyzer.models import PromptTrack

    tracks = list(
        PromptTrack.objects.filter(analysis_run=run, deleted_at__isnull=True)
        .prefetch_related("results", "results__citations")
    )

    tasks: list[dict] = []
    lost: list[tuple[PromptTrack, list[str]]] = []  # (track, engines that missed)
    competitor_domains: dict[str, int] = {}
    gap_domains: dict[str, int] = {}

    for track in tracks:
        results = list(track.results.all())
        if not results:
            continue  # never fired -> unknown, not "lost"
        mentioned = [r for r in results if r.brand_mentioned]
        if mentioned:
            continue  # brand appears somewhere -> not a loss
        engines = sorted({r.get_engine_display() for r in results})
        lost.append((track, engines))
        # Collect who *is* cited when the brand is absent.
        for r in results:
            for c in r.citations.all():
                if c.is_brand:
                    continue
                if c.is_competitor and c.domain:
                    competitor_domains[c.domain] = competitor_domains.get(c.domain, 0) + 1
                elif c.domain and not _is_platform_domain(c.domain):
                    # Skip open platforms (medium/youtube/reddit/…) — "get mentioned
                    # there" is not a discrete, verifiable task and any real brand is
                    # already present.
                    gap_domains[c.domain] = gap_domains.get(c.domain, 0) + 1

    total = len(tracks)
    lost_count = len(lost)

    # ── 1. Prompts the brand is losing (highest value) ──
    for track, engines in lost[:_MAX_LOST_PROMPTS]:
        prompt = track.prompt_text.strip()
        engine_list = ", ".join(engines)
        task = _base(CODE_PROMPT_LOST, "ai_visibility", "high")
        task.update({
            "title": f"Win the AI query: \"{prompt[:80]}\"",
            "description": (
                f"You are not cited for this tracked prompt on {engine_list}. "
                f"This is a real query buyers ask - and AI engines answer it with other sources."
            ),
            "action": (
                "Publish an answer-first page that directly and comprehensively answers this "
                "exact query: lead with a 2-3 sentence direct answer, add a comparison table and "
                "FAQ, cite authoritative sources, and mark it up with FAQPage/Article schema. "
                "Then re-check visibility for this prompt."
            ),
            "steps": _prompt_lost_steps(prompt, engine_list),
            "evidence": {"prompt": prompt, "engines": engines, "brand_mentions": 0},
        })
        tasks.append(task)

    # ── 2. Competitors cited where the brand is absent ──
    if competitor_domains:
        top = sorted(competitor_domains.items(), key=lambda kv: -kv[1])[:5]
        names = ", ".join(d for d, _ in top[:3])
        task = _base(CODE_COMPETITOR_CITED, "ai_visibility", "high")
        task.update({
            "title": "Close the citation gap with competitors AI recommends",
            "description": (
                f"On prompts where you are absent, AI engines cite competitors such as {names}. "
                f"These sources are winning the recommendation you should own."
            ),
            "action": (
                "Study what those competitor pages do that you don't (depth, comparisons, reviews, "
                "schema, third-party mentions), then close the gap on your equivalent pages and "
                "earn mentions on the same high-authority sources."
            ),
            "steps": _competitor_cited_steps(names),
            "evidence": {"competitor_domains": dict(top)},
        })
        tasks.append(task)

    # ── 3. Recurring citation-domain gaps (get mentioned there) ──
    # Consider more candidates than we emit, so brand-presence suppression doesn't
    # leave the section empty. For each, verify per-brand: if the brand is ALREADY
    # on that domain (Vercel on TechCrunch), it's not a gap — skip it.
    brand_name = (run.brand_name or "").strip()
    candidates = sorted(
        ((d, n) for d, n in gap_domains.items() if n >= 2),
        key=lambda kv: -kv[1],
    )[:6]
    emitted = 0
    for domain, count in candidates:
        if emitted >= _MAX_CITATION_GAPS:
            break
        if brand_present_on_domain(brand_name, domain, industry=industry) is True:
            continue  # already present for THIS brand → not a real gap
        task = _base(CODE_CITATION_GAP, "entity", "medium")
        task.update({
            "title": f"Get mentioned on {domain}",
            "description": (
                f"AI engines repeatedly cite {domain} ({count}x) when answering your tracked "
                f"prompts, but never cite you. Earning a presence there feeds AI responses."
            ),
            "action": (
                f"Pursue a legitimate mention on {domain} - a listing, guest contribution, review, "
                f"or editorial mention as appropriate for that source. AI re-indexes these regularly."
            ),
            "steps": _citation_gap_steps(domain, count),
            "evidence": {"domain": domain, "citations": count},
        })
        tasks.append(task)
        emitted += 1

    # ── 4. Competitor pillar gap ──
    pillar_gap_task = _competitor_pillar_gap_task(run, industry)
    if pillar_gap_task is not None:
        tasks.append(pillar_gap_task)

    logger.info(
        "Run %s: generated %d GEO-signal tasks (%d/%d prompts lost)",
        getattr(run, "id", "?"), len(tasks), lost_count, total,
    )
    return tasks


def _competitor_pillar_gap_task(run, industry: str) -> dict | None:
    """Emit a task when a competitor materially out-scores the brand's homepage."""
    from apps.analyzer.models import Competitor, PageScore

    ps = PageScore.objects.filter(analysis_run=run).order_by("id").first()
    if not ps or not ps.composite_score:
        return None
    comps = list(
        Competitor.objects.filter(analysis_run=run, scored=True, composite_score__isnull=False)
        .order_by("-composite_score")
    )
    if not comps:
        return None
    top = comps[0]
    gap = float(top.composite_score) - float(ps.composite_score)
    if gap < 8.0:  # not a material gap
        return None
    task = _base(CODE_COMPETITOR_PILLAR_GAP, "ai_visibility", "medium")
    task.update({
        "title": f"Outrank {top.name} on AI-readiness",
        "description": (
            f"{top.name} scores {top.composite_score:.0f} vs your {ps.composite_score:.0f} on "
            f"overall AI-readiness - a {gap:.0f}-point gap that shapes which brand AI recommends."
        ),
        "action": (
            "Prioritise the pillars where the gap is widest (usually content depth, E-E-A-T, and "
            "schema). Complete the on-page fixes in your task list, then re-analyse to confirm the "
            "gap has closed."
        ),
        "steps": _competitor_pillar_gap_steps(top.name),
        "evidence": {"competitor": top.name, "competitor_score": round(float(top.composite_score), 1),
                     "brand_score": round(float(ps.composite_score), 1), "gap": round(gap, 1)},
    })
    return task


def sync_geo_signal_tasks(run, industry: str = "default") -> int:
    """Idempotently replace this run's GEO-signal Recommendations. Returns count.

    Safe to call repeatedly (e.g. after each prompt re-check): existing geo_signal
    rows for the run are removed and rebuilt from current measurements. On-page
    (analyzer) and ai_insight recommendations are untouched.
    """
    from django.db import transaction

    from apps.analyzer.models import Recommendation, UserAction

    tasks = generate_geo_signal_tasks(run, industry=industry)
    with transaction.atomic():
        # Remove materialized tasks for the old GEO recs first: UserAction.recommendation
        # is SET_NULL, so deleting the recs alone would orphan (not remove) their tasks.
        UserAction.objects.filter(
            analysis_run=run, recommendation__source=Recommendation.Source.GEO_SIGNAL
        ).delete()
        run.recommendations.filter(source=Recommendation.Source.GEO_SIGNAL).delete()
        if tasks:
            Recommendation.objects.bulk_create(
                [Recommendation(analysis_run=run, **t) for t in tasks]
            )
    return len(tasks)
