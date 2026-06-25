"""
Which analyzer findings the GitHub code agent can fix.

Single source of truth for "code_fixable" (shown on the Fixes page) and for which
finding codes the fix endpoint will accept. Derived from the analyzer's
recommendation catalog so it stays in sync as findings are added.
"""

from apps.analyzer.pipeline.recommendations import OFFPAGE_FINDINGS, RECOMMENDATION_RULES

# Findings an in-repo agent fundamentally cannot fix by editing files:
#  - off-page presence (Wikipedia, Reddit, social, brand-in-AI) — OFFPAGE_FINDINGS
#  - infrastructure: TLS cert, hosting/perf, server crawl responses
INFRA_NOT_FIXABLE = {
    "no_https",
    "slow_load_time",
    "crawl_failed",
    "crawl_blocked_403",
    "crawl_timeout",
}
NOT_CODE_FIXABLE: set[str] = set(OFFPAGE_FINDINGS) | INFRA_NOT_FIXABLE

# Everything else in the catalog is, in principle, a code/content edit (~72 codes).
CODE_FIXABLE: set[str] = set(RECOMMENDATION_RULES) - NOT_CODE_FIXABLE

# Phase-1 scope: discrete, low-risk structural edits the agent does reliably.
# Prose/content rewrites (no_citations, weak_authoritative_tone, thin_product_description,
# FAQ copy, expert quotes…) are in CODE_FIXABLE but deferred to phase 2. The
# ``& CODE_FIXABLE`` filter drops any stale key if the catalog changes.
STRUCTURAL: set[str] = {
    # technical
    "no_llms_txt",
    "ai_bots_blocked",
    "no_meta_description",
    "no_og_tags",
    "no_canonical",
    "no_viewport",
    "no_sitemap",
    "meta_noindex",
    # schema
    "no_jsonld",
    "invalid_jsonld_structure",
    "no_organization_schema",
    "incomplete_organization_schema",
    "no_article_schema",
    "incomplete_article_schema",
    "no_faqpage_schema",
    "no_breadcrumb_schema",
    "no_product_schema",
    "no_review_schema",
    "no_local_business_schema",
    # eeat (structural, not prose)
    "no_author",
    "no_author_bio",
    "no_publish_date",
    "no_updated_date",
    "no_about_page",
    # content (structural)
    "no_h1",
    "multiple_h1",
    "broken_heading_hierarchy",
} & CODE_FIXABLE

# The set the agent attempts AND the UI offers as "Fix with AI". We let the agent
# attempt anything that could be a code/content edit (CODE_FIXABLE) and let it
# REVIEW each one at fix time — it calls cannot_fix (→ stays Manual) when a fix
# would require fabricating facts it can't source from the repo. Only off-page +
# infra findings (NOT_CODE_FIXABLE) are never offered. STRUCTURAL is kept as the
# high-confidence subset for reference/telemetry.
AGENT_FIXABLE: set[str] = CODE_FIXABLE


def is_agent_fixable(finding_code: str) -> bool:
    """Whether the code agent should OFFER to fix this finding.

    Deliberately NOT gated on the static catalog — findings can be anything in the
    future. Anything that isn't clearly off-page or infrastructure is offered, and
    the agent itself REVIEWS each one at fix time, calling cannot_fix (→ stays
    Manual, with a reason) when it can't — e.g. it would need fabricated facts.
    Only the known not-code-fixable set (off-page presence + infra) is never offered.
    """
    return bool(finding_code) and finding_code not in NOT_CODE_FIXABLE
