"""Citation gaps: the domains answer engines cite instead of you.

For a brand with no authority, on-page work has a ceiling. The engine retrieves
whatever already ranks, so getting *into those pages* beats publishing more of
your own. This ranks the domains that win your prompts and turns them into an
outreach list.

It is the strongest signal the product has, because it is **observed**: these are
the exact sources engines returned when they answered your tracked prompts and
did not mention you. Nothing here is inferred from a model's opinion of your
industry.

Two design points:

* **Ranked by distinct prompts won, not citation count.** A domain quoted five
  times inside one answer is one prompt, not five. Counting raw citations would
  promote whichever source happens to be verbose.
* **"Live" is verified, never self-reported.** A user can mark a target
  *pitched* or *dismissed*, but they cannot mark it done: that status is derived
  from ``brand_present_on_domain``, the same Serper check used elsewhere. An
  outreach tracker that trusts its own checkboxes drifts out of date within
  weeks.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

logger = logging.getLogger("apps")

# Ranked list length. Outreach is manual work; a list of eighty targets is a list
# nobody starts.
MAX_TARGETS = 15

# A domain must win at least this many prompts to be worth pitching. One is
# usually a coincidence of a single answer.
MIN_PROMPTS = 1

IDENTIFIED = "identified"
PITCHED = "pitched"
DISMISSED = "dismissed"
LIVE = "live"

USER_SETTABLE = {IDENTIFIED, PITCHED, DISMISSED}


@dataclass
class CitationGap:
    domain: str
    prompts_won: int
    citations: int
    example_prompts: list[str] = field(default_factory=list)
    example_url: str = ""
    status: str = IDENTIFIED
    brand_present: bool | None = None
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# Academic and scholarly publishers. Engines cite them heavily for definitional
# and research-flavoured prompts, and they are real citations - but you cannot
# pitch your way into ScienceDirect. Listing them as outreach targets makes a
# quarter of the queue unactionable and teaches users to distrust the rest.
#
# Matched by suffix so subdomains (link.springer.com, eureka.patsnap.com) resolve
# without enumerating every one.
_ACADEMIC_SUFFIXES = (
    "sciencedirect.com",
    "springer.com",
    "researchgate.net",
    "academia.edu",
    "jstor.org",
    "nature.com",
    "wiley.com",
    "tandfonline.com",
    "sagepub.com",
    "ieee.org",
    "acm.org",
    "arxiv.org",
    "ssrn.com",
    "pubmed.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "semanticscholar.org",
    "patsnap.com",
    "mdpi.com",
    "frontiersin.org",
    "plos.org",
    "biomedcentral.com",
    "elsevier.com",
    "emerald.com",
    ".edu",
    ".ac.uk",
)


def _platform_domains() -> frozenset[str]:
    """Open platforms that are not discrete outreach targets.

    Shared with the GEO task generator rather than re-listed: "get mentioned on
    medium.com" is not a task anyone can complete, and any established brand is
    already there. If a third consumer appears this should move to a shared
    constants module.
    """
    from apps.analyzer.services.geo_tasks import _PLATFORM_DOMAINS

    return _PLATFORM_DOMAINS


def is_reachable_target(domain: str) -> bool:
    """Whether a brand could realistically earn a placement on this domain.

    A citation is not automatically an opportunity. Open platforms and academic
    publishers are cited constantly and cannot be pitched, so listing them turns
    an outreach queue into a reading list.
    """
    clean = (domain or "").lower().removeprefix("www.")
    if not clean:
        return False
    if clean in _platform_domains():
        return False
    return not any(
        clean == suffix.lstrip(".") or clean.endswith(suffix if suffix.startswith(".") else f".{suffix}")
        for suffix in _ACADEMIC_SUFFIXES
    )


def collect_gaps(run) -> list[CitationGap]:
    """Domains cited when the brand was not, ranked by how many prompts they win.

    Only prompts the brand lost outright count. A prompt where the brand was
    already mentioned is not a gap, however many other sources were cited
    alongside it.
    """
    from apps.analyzer.models import PromptTrack

    own_domain = ""
    try:
        from urllib.parse import urlparse

        own_domain = urlparse(run.url or "").netloc.lower().removeprefix("www.")
    except Exception:
        pass

    prompts_by_domain: dict[str, set[int]] = {}
    citations_by_domain: dict[str, int] = {}
    example_url: dict[str, str] = {}

    tracks = PromptTrack.objects.filter(analysis_run=run, deleted_at__isnull=True).prefetch_related(
        "results__citations"
    )
    prompt_text: dict[int, str] = {}

    for track in tracks:
        results = list(track.results.all())
        if not results:
            continue  # never fired -> unknown, not a loss
        if any(r.brand_mentioned for r in results):
            continue  # brand appears somewhere -> not a gap
        prompt_text[track.id] = track.prompt_text or ""

        for result in results:
            for citation in result.citations.all():
                domain = (citation.domain or "").lower().removeprefix("www.")
                if not domain or citation.is_brand:
                    continue
                if domain == own_domain or not is_reachable_target(domain):
                    continue
                prompts_by_domain.setdefault(domain, set()).add(track.id)
                citations_by_domain[domain] = citations_by_domain.get(domain, 0) + 1
                example_url.setdefault(domain, citation.url or "")

    gaps = [
        CitationGap(
            domain=domain,
            prompts_won=len(track_ids),
            citations=citations_by_domain.get(domain, 0),
            example_prompts=[prompt_text.get(t, "") for t in sorted(track_ids)][:3],
            example_url=example_url.get(domain, ""),
        )
        for domain, track_ids in prompts_by_domain.items()
        if len(track_ids) >= MIN_PROMPTS
    ]
    gaps.sort(key=lambda g: (-g.prompts_won, -g.citations, g.domain))
    return gaps[:MAX_TARGETS]


def _stored_status(org, domains: list[str]) -> dict[str, tuple[str, str]]:
    """{domain: (status, note)} for whatever the user has already set."""
    from apps.analyzer.models import CitationOutreach

    if org is None or not domains:
        return {}
    rows = CitationOutreach.objects.filter(organization=org, domain__in=domains)
    return {r.domain: (r.status, r.note) for r in rows}


def _verify_live(brand: str, domain: str, industry: str = "") -> bool | None:
    """Whether the brand is now present on the domain. ``None`` = unknown."""
    from apps.analyzer.pipeline.offpage_presence import brand_present_on_domain

    try:
        return brand_present_on_domain(brand, domain, industry=industry)
    except Exception:
        logger.warning("citation_gaps: presence check failed for %s", domain, exc_info=True)
        return None


def report_for_run(run, *, verify: bool = True) -> dict:
    """Ranked citation-gap outreach list for a run. Never raises.

    ``verify=False`` skips the per-domain presence checks, which cost one search
    each. Useful for a fast read where only the ranking matters.
    """
    try:
        gaps = collect_gaps(run)
    except Exception:
        logger.exception("citation_gaps: collection failed for run %s", getattr(run, "id", "?"))
        return {"targets": [], "summary": {"total": 0, "prompts_lost": 0, "live": 0}}

    org = getattr(run, "organization", None)
    stored = _stored_status(org, [g.domain for g in gaps])
    brand = (getattr(run, "brand_name", "") or "").strip()

    for gap in gaps:
        status, note = stored.get(gap.domain, (IDENTIFIED, ""))
        gap.note = note
        # A user may mark pitched or dismissed; "live" is only ever earned by
        # actually appearing on the domain.
        if verify and brand and status != DISMISSED:
            gap.brand_present = _verify_live(brand, gap.domain)
            gap.status = LIVE if gap.brand_present is True else status
        else:
            gap.status = status

    lost = len({p for g in gaps for p in g.example_prompts if p})
    return {
        "targets": [g.as_dict() for g in gaps],
        "summary": {
            "total": len(gaps),
            "prompts_lost": lost,
            "live": sum(1 for g in gaps if g.status == LIVE),
            "pitched": sum(1 for g in gaps if g.status == PITCHED),
        },
    }


def set_status(org, domain: str, status: str, note: str = "") -> dict:
    """Record the user's outreach state for one domain.

    ``live`` is rejected: it is derived from a presence check, not claimed. That
    keeps the pipeline honest as it ages.
    """
    from apps.analyzer.models import CitationOutreach

    clean = (domain or "").strip().lower().removeprefix("www.")
    if not clean:
        raise ValueError("domain is required")
    if status not in USER_SETTABLE:
        raise ValueError(
            f"status must be one of {sorted(USER_SETTABLE)}; 'live' is verified, not set"
        )

    row, _created = CitationOutreach.objects.update_or_create(
        organization=org,
        domain=clean,
        defaults={"status": status, "note": note[:500]},
    )
    return {"domain": row.domain, "status": row.status, "note": row.note}
