"""AI crawler access: are the answer engines allowed in, and do they actually come?

Prompt tracking measures the outcome ("we were not cited"). This measures the
first cause. An engine can only cite a page it has fetched, so before content,
authority or off-page work is worth anything, the crawler has to have been here.

Two halves that already existed separately and were never joined:

* **Policy** - what ``robots.txt`` permits. Already parsed by
  ``pipeline/technical._check_robots_allows_ai``.
* **Reality** - what actually visited, from ``CrawlerHit`` rows posted by the
  site's edge/log integration.

Policy alone is misleading in both directions. "GPTBot is allowed" says nothing
about whether it ever came, and a site can be perfectly configured yet never
crawled because nothing links to it. Reality alone is equally misleading: zero
hits looks identical whether the bot was blocked, never discovered, or the
telemetry integration was simply never installed.

Reported per **engine**, not per user agent, because "ChatGPT cannot see you" is
the fact the customer needs; "OAI-SearchBot is disallowed" is the implementation
detail underneath it.

**No telemetry is not zero traffic.** An organization that never installed the
crawler ingest gets ``unknown``, never "never crawled" - the same contract used
everywhere else in this pipeline. Telling someone their site is uncrawled when
we simply cannot see is worse than saying nothing.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger("apps")

# Days of crawler telemetry to consider, and how long since the last visit before
# an otherwise-healthy bot counts as stale. AI crawlers re-fetch active sites well
# inside 30 days, so silence beyond that is a real signal rather than noise.
WINDOW_DAYS = 30
STALE_AFTER_DAYS = 14

# user-agent token -> the product a customer recognises. Several engines run more
# than one agent for different jobs (training corpus vs live answer retrieval),
# and they are worth distinguishing: being open to one and closed to the other is
# a common and costly misconfiguration.
BOT_ENGINES: dict[str, dict] = {
    "OAI-SearchBot": {"engine": "ChatGPT", "role": "search", "why": "Fetches pages to answer live ChatGPT searches."},
    "GPTBot": {"engine": "ChatGPT", "role": "training", "why": "Collects pages for OpenAI model training."},
    "ChatGPT-User": {"engine": "ChatGPT", "role": "browse", "why": "Fetches a page when a user asks ChatGPT to open it."},
    "ClaudeBot": {"engine": "Claude", "role": "training", "why": "Collects pages for Anthropic model training."},
    "Claude-SearchBot": {"engine": "Claude", "role": "search", "why": "Fetches pages to answer live Claude searches."},
    "Google-Extended": {"engine": "Gemini / AI Overviews", "role": "grounding", "why": "Controls whether Google may use the page to ground AI answers."},
    "PerplexityBot": {"engine": "Perplexity", "role": "search", "why": "Builds Perplexity's own index."},
    "Perplexity-User": {"engine": "Perplexity", "role": "browse", "why": "Fetches a page on behalf of a Perplexity user."},
    "CCBot": {"engine": "Common Crawl", "role": "training", "why": "Feeds the open corpus many models train on."},
    "Bingbot": {"engine": "Bing / Copilot", "role": "search", "why": "Bing's index, which ChatGPT search leans on."},
    "meta-externalagent": {"engine": "Meta AI", "role": "training", "why": "Collects pages for Meta model training."},
    "Applebot-Extended": {"engine": "Apple Intelligence", "role": "training", "why": "Controls Apple's AI use of the page."},
}

# Status values, ordered worst-first for display.
BLOCKED = "blocked"
NEVER_SEEN = "allowed_never_crawled"
STALE = "allowed_stale"
ACTIVE = "active"
UNKNOWN = "unknown"

_SEVERITY = {BLOCKED: 0, NEVER_SEEN: 1, STALE: 2, ACTIVE: 3, UNKNOWN: 4}

_DIAGNOSIS = {
    BLOCKED: "robots.txt disallows this crawler. Nothing on the site can be cited by this engine until that is changed.",
    NEVER_SEEN: "Allowed, but it has never fetched a page. The site is reachable in principle and undiscovered in practice - usually too few inbound links, or not in the index this engine reads.",
    STALE: "Allowed and it has visited before, but not recently. Content published since then is unlikely to be known to this engine.",
    ACTIVE: "Allowed and fetching pages recently. Retrievability is not the bottleneck for this engine.",
    UNKNOWN: "No crawler telemetry for this site, so visits cannot be confirmed either way. Install the crawler log integration to measure this.",
}


@dataclass
class EngineAccess:
    bot: str
    engine: str
    role: str
    why: str
    status: str
    allowed: bool | None
    hits: int = 0
    distinct_paths: int = 0
    last_seen: str = ""
    days_since_last_seen: int | None = None
    diagnosis: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class CrawlerAccessReport:
    has_telemetry: bool
    window_days: int
    engines: list[EngineAccess] = field(default_factory=list)
    uncrawled_pages: list[str] = field(default_factory=list)
    robots_found: bool = False

    def as_dict(self) -> dict:
        data = asdict(self)
        data["engines"] = [e.as_dict() for e in self.engines]
        data["summary"] = self.summary()
        return data

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for e in self.engines:
            counts[e.status] = counts.get(e.status, 0) + 1
        blocked = [e.engine for e in self.engines if e.status == BLOCKED]
        never = [e.engine for e in self.engines if e.status == NEVER_SEEN]
        return {
            "counts": counts,
            "blocked_engines": sorted(set(blocked)),
            "never_crawled_engines": sorted(set(never)),
            "uncrawled_page_count": len(self.uncrawled_pages),
        }


def _wildcard_disallows_everything(robots_txt: str) -> bool:
    """True when ``User-agent: *`` carries a site-wide ``Disallow: /``."""
    agent = None
    for raw in (robots_txt or "").lower().splitlines():
        line = raw.strip()
        if line.startswith("user-agent:"):
            agent = line.split(":", 1)[1].strip()
        elif line.startswith("disallow:") and agent == "*":
            if line.split(":", 1)[1].strip() in {"/", "/*"}:
                return True
    return False


def _blocked_bots(robots_txt: str) -> tuple[set[str], bool]:
    """Lowercased bot tokens robots.txt disallows, and whether robots.txt exists.

    Delegates per-agent parsing to the analyzer's existing checker so policy is
    read one way across the product. That checker matches against its own
    hardcoded agent list, which does not yet include newer agents such as
    ``OAI-SearchBot`` - the one that matters most for ChatGPT search - so a
    site-wide wildcard block is detected here as well and applied to every agent
    this module knows about. Without it a blanket ``Disallow: /`` would be
    reported as blocking only the handful of bots that list happens to contain.
    """
    from apps.analyzer.pipeline.technical import _check_robots_allows_ai

    if not (robots_txt or "").strip():
        return set(), False

    if _wildcard_disallows_everything(robots_txt):
        return {b.lower() for b in BOT_ENGINES}, True

    _allows, blocked = _check_robots_allows_ai(robots_txt)
    return {b.lower() for b in blocked}, True


def _hits_by_bot(org, since):
    """{lowercased bot token: (hits, distinct paths, last seen)} in the window."""
    from django.db.models import Count, Max

    from apps.analyzer.models import CrawlerHit

    rows = (
        CrawlerHit.objects.filter(organization=org, hit_at__gte=since)
        .values("bot")
        .annotate(hits=Count("id"), paths=Count("path", distinct=True), last=Max("hit_at"))
    )
    return {
        (r["bot"] or "").lower(): (r["hits"], r["paths"], r["last"])
        for r in rows
        if r["bot"]
    }


def _uncrawled_pages(org, since, known_urls: list[str]) -> list[str]:
    """Known pages no AI crawler fetched in the window.

    Path-matched, because telemetry records a path while page scores record a
    full URL.
    """
    from urllib.parse import urlparse

    from apps.analyzer.models import CrawlerHit

    if not known_urls:
        return []
    seen = {
        (p or "/").rstrip("/") or "/"
        for p in CrawlerHit.objects.filter(organization=org, hit_at__gte=since)
        .values_list("path", flat=True)
        .distinct()
    }
    missing = []
    for url in known_urls:
        path = (urlparse(url).path or "/").rstrip("/") or "/"
        if path not in seen:
            missing.append(url)
    return missing


def build_report(org, robots_txt: str = "", known_urls: list[str] | None = None) -> CrawlerAccessReport:
    """Join robots.txt policy with observed crawler visits for one organization."""
    since = timezone.now() - timedelta(days=WINDOW_DAYS)
    blocked, robots_found = _blocked_bots(robots_txt)

    try:
        hits = _hits_by_bot(org, since)
    except Exception:
        logger.exception("crawler_access: telemetry query failed")
        hits = {}

    has_telemetry = bool(hits)
    now = timezone.now()
    engines: list[EngineAccess] = []

    for bot, meta in BOT_ENGINES.items():
        token = bot.lower()
        is_blocked = any(token in b or b in token for b in blocked)
        hit_count, paths, last = hits.get(token, (0, 0, None))

        if is_blocked:
            status, allowed = BLOCKED, False
        elif not has_telemetry:
            # No rows at all for this org: we cannot distinguish "never came" from
            # "we are not watching". Say so instead of guessing.
            status, allowed = UNKNOWN, True
        elif hit_count == 0:
            status, allowed = NEVER_SEEN, True
        else:
            days = (now - last).days if last else None
            status = STALE if days is not None and days > STALE_AFTER_DAYS else ACTIVE
            allowed = True

        days_since = (now - last).days if last else None
        engines.append(
            EngineAccess(
                bot=bot,
                engine=meta["engine"],
                role=meta["role"],
                why=meta["why"],
                status=status,
                allowed=allowed,
                hits=hit_count,
                distinct_paths=paths,
                last_seen=last.isoformat() if last else "",
                days_since_last_seen=days_since,
                diagnosis=_DIAGNOSIS[status],
            )
        )

    engines.sort(key=lambda e: (_SEVERITY[e.status], e.engine))

    uncrawled: list[str] = []
    if has_telemetry and known_urls:
        try:
            uncrawled = _uncrawled_pages(org, since, known_urls)
        except Exception:
            logger.exception("crawler_access: uncrawled-page check failed")

    return CrawlerAccessReport(
        has_telemetry=has_telemetry,
        window_days=WINDOW_DAYS,
        engines=engines,
        uncrawled_pages=uncrawled[:25],
        robots_found=robots_found,
    )


def to_recommendations(report: CrawlerAccessReport) -> list[dict]:
    """Turn blocked/never-crawled engines into rec-shaped tasks.

    This belongs in the task list rather than a dashboard tile because it is the
    highest-severity finding the product can make: a blocked crawler caps every
    other effort at zero. Cloudflare ships its "AI Scrapers and Crawlers" block
    on by default and injects the Disallow rules into robots.txt, so a site can
    be blocking every AI engine without anyone having chosen to.

    Only definite states produce tasks. ``unknown`` never does - telling someone
    to fix a crawler we cannot see would be advice built on missing data.
    """
    recs: list[dict] = []

    blocked = [e for e in report.engines if e.status == BLOCKED]
    if blocked:
        names = sorted({e.engine for e in blocked})
        agents = sorted({e.bot for e in blocked})
        recs.append(
            {
                "finding_code": "ai_crawler_blocked",
                "pillar": "technical",
                "priority": "critical",
                "category": "technical",
                "source": "analyzer",
                "title": f"robots.txt blocks {len(names)} AI engine(s) from your site",
                "description": (
                    f"{', '.join(names)} are disallowed in robots.txt "
                    f"({', '.join(agents)}). These engines cannot fetch your pages, so "
                    f"they cannot cite you no matter how good the content is."
                ),
                "action": (
                    "Remove the site-wide Disallow rules for these agents. If your robots.txt "
                    "contains a 'Cloudflare Managed Content' block, the rules are injected by "
                    "Cloudflare, not by your app: turn off the AI crawler block for this zone "
                    "in the Cloudflare dashboard. Keep exactly one group per user-agent - two "
                    "groups for the same agent is ambiguous and crawlers resolve it differently."
                ),
                "why": "A blocked crawler cannot fetch the page, so no amount of content or authority work can earn a citation.",
                "evidence": {"blocked_agents": agents, "engines": names},
                "difficulty": "easy",
                "estimated_minutes": 10,
                "xp_reward": 100,
            }
        )

    never = [e for e in report.engines if e.status == NEVER_SEEN]
    if never:
        names = sorted({e.engine for e in never})
        recs.append(
            {
                "finding_code": "ai_crawler_never_visited",
                "pillar": "technical",
                "priority": "high",
                "category": "technical",
                "source": "analyzer",
                "title": f"{len(names)} AI engine(s) have never crawled your site",
                "description": (
                    f"{', '.join(names)} are allowed by robots.txt but have not fetched a "
                    f"single page in the last {report.window_days} days. The site is reachable "
                    f"in principle and undiscovered in practice."
                ),
                "action": (
                    "Earn inbound links from pages these engines already crawl, submit the "
                    "sitemap to the index each engine reads (Bing Webmaster Tools for ChatGPT "
                    "search, Search Console for Gemini), and confirm the pages render without "
                    "JavaScript."
                ),
                "why": "An engine that has never fetched a page has nothing of yours to cite.",
                "evidence": {"engines": names, "window_days": report.window_days},
                "difficulty": "medium",
                "estimated_minutes": 45,
                "xp_reward": 70,
            }
        )

    return recs


def report_for_run(run) -> dict:
    """Crawler-access report for a run's organization. Never raises."""
    try:
        org = getattr(run, "organization", None)
        if org is None:
            return CrawlerAccessReport(has_telemetry=False, window_days=WINDOW_DAYS).as_dict()

        robots_txt = ""
        try:
            from apps.analyzer.pipeline.crawler import fetch_file_content

            robots_txt = fetch_file_content(run.url, "robots.txt") or ""
        except Exception:
            logger.warning("crawler_access: robots.txt fetch failed for %s", run.url, exc_info=True)

        known = list(run.page_scores.values_list("url", flat=True))
        return build_report(org, robots_txt=robots_txt, known_urls=known).as_dict()
    except Exception:
        logger.exception("crawler_access: report failed for run %s", getattr(run, "id", "?"))
        return CrawlerAccessReport(has_telemetry=False, window_days=WINDOW_DAYS).as_dict()
