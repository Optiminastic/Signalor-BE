"""Module-level helpers and constants shared across the view modules.

Extracted verbatim from the original 8,409-line views.py. Several of these
are domain-specific (blog, crawl) and should migrate into services/ as each
resource is properly layered - keeping them together here first makes the
split itself a pure move with nothing else changing.
"""

import logging
from urllib.parse import urlparse

from django.db import close_old_connections
from rest_framework import status
from rest_framework.response import Response

from apps.accounts.subscription_utils import (
    get_plan_limits,
    is_plan_limits_enforcement_enabled,
)
from apps.integrations.models import Integration

from ..models import (
    AnalysisRun,
    PromptResult,
    PromptTrack,
)
from ..services.blog_automation import (  # noqa: F401
    BLOG_MODEL_PROVIDER,
    _auto_can_add_today,
    _blog_run_email,
    _blog_source_candidates,
    _brand_ref_for_run,
    _clean_blog_posts,
    _enqueue_daily_jobs,
    _extract_blog_json,
    _generate_blog_draft,
    _generate_blog_html,
    _generate_blog_topics,
    _get_or_create_blog_config,
    _meter_blog_spend,
    _parse_publish_time,
    _process_due_blog_jobs,
    _publish_blog_job,
    _resolve_blog_integration,
    _short_title,
    _slugify,
    _to_html_from_markdownish,
)
from ..services.site_resolution import (  # noqa: F401
    _normalize_origin,
    _resolve_crawl_site,
    _safe_first,
)

logger = logging.getLogger("apps")

CRAWL_CHECK_USER_AGENT = "SignalorBot/1.0 (+https://signalor.ai; crawl-essentials-check)"

def _evaluate_crawl_file(key: str, label: str, target_url: str, content: str, status_code: int | None):
    text = (content or "").strip()
    lower = text.lower()
    exists = bool(text) and status_code == 200
    issues = []
    recommendations = []

    if not exists:
        if key == "llms":
            recommendations = [
                "Create /llms.txt with clear site summary and key content sections.",
                "Include preferred crawling guidance and support/contact section.",
            ]
        elif key == "robots":
            recommendations = [
                "Create /robots.txt with User-agent rules and sitemap reference.",
                "Ensure important content is crawlable and admin paths are restricted.",
            ]
        else:
            recommendations = [
                "Publish /sitemap.xml from CMS or SEO tooling.",
                "Include canonical URLs and keep it updated automatically.",
            ]
        return {
            "key": key,
            "label": label,
            "url": target_url,
            "found": False,
            "status": "missing",
            "http_status": status_code,
            "score": 0,
            "issues": ["File not found at expected URL."],
            "recommendations": recommendations,
            "excerpt": "",
        }

    if key == "robots":
        if "user-agent:" not in lower:
            issues.append("Missing User-agent directive.")
        if "sitemap:" not in lower:
            issues.append("Missing Sitemap declaration.")
        if "disallow:" not in lower and "allow:" not in lower:
            issues.append("Missing Allow/Disallow directives.")
        recommendations = [
            "Keep at least one User-agent rule block.",
            "Add Sitemap: https://yourdomain.com/sitemap.xml",
            "Review blocked paths to avoid hiding key pages.",
        ]
    elif key == "sitemap":
        if "<urlset" not in lower and "<sitemapindex" not in lower:
            issues.append("Invalid sitemap XML structure.")
        if "<loc>" not in lower:
            issues.append("No URL locations (<loc>) found.")
        recommendations = [
            "Serve valid XML using <urlset> or <sitemapindex>.",
            "Include canonical URLs and refresh after content updates.",
        ]
    else:
        if len(text) < 120:
            issues.append("Content is too short to guide AI crawlers.")
        if "#" not in text:
            issues.append("Missing section headers for structure.")
        if "contact" not in lower and "support" not in lower:
            issues.append("Missing contact/support guidance.")
        recommendations = [
            "Document your site purpose, key sections, and content boundaries.",
            "Use headings and concise policies for AI model usage guidance.",
        ]

    score = max(0, 100 - (len(issues) * 30))
    status_value = "good" if not issues else "needs_improvement"
    excerpt = text[:600]

    return {
        "key": key,
        "label": label,
        "url": target_url,
        "found": True,
        "status": status_value,
        "http_status": status_code,
        "score": score,
        "issues": issues,
        "recommendations": recommendations,
        "excerpt": excerpt,
    }

def _fire_and_save_prompt(track: PromptTrack, brand_name: str, brand_url: str):
    """Background worker: fires prompt across engines and saves PromptResult rows."""
    from django.db import close_old_connections

    from ..pipeline.citations import competitor_hosts_for_run, host_of, persist_prompt_result
    from ..pipeline.prompt_tracker import fire_prompt_across_engines

    close_old_connections()
    try:
        em = (track.analysis_run.email or "").strip()
        allowed = get_plan_limits(em)["engines"] if is_plan_limits_enforcement_enabled() and em else None
        engine_results = fire_prompt_across_engines(
            track.prompt_text, brand_name, brand_url, allowed_engines=allowed
        )
        brand_host = host_of(brand_url)
        rival_hosts = competitor_hosts_for_run(track.analysis_run)
        for r in engine_results:
            persist_prompt_result(track, r, brand_host, rival_hosts)
        logger.info("PromptTrack #%d: %d engine results saved", track.pk, len(engine_results))

        # Compute and persist 5-factor scores
        from ..pipeline.prompt_tracker import compute_prompt_score

        all_res = list(
            track.results.values("brand_mentioned", "sentiment", "rank_position", "confidence", "engine")
        )
        sd = compute_prompt_score(all_res)
        track.score = sd["score"]
        track.authority_score = sd["authority_score"]
        track.content_quality_score = sd["content_quality_score"]
        track.structural_score = sd["structural_score"]
        track.semantic_score = sd["semantic_score"]
        track.third_party_score = sd["third_party_score"]
        track.save(
            update_fields=[
                "score",
                "authority_score",
                "content_quality_score",
                "structural_score",
                "semantic_score",
                "third_party_score",
            ]
        )
        from core.cache.keys import invalidate_run_aggregates

        invalidate_run_aggregates(track.analysis_run.slug)

        # A prompt the brand loses should produce a task for it. Results land
        # here asynchronously, well after the analysis that built the current
        # task set, so the prompt-driven tasks are rebuilt from what was just
        # measured. Best-effort: never fail the prompt over a task refresh.
        try:
            from ..services.geo_tasks import sync_geo_signal_tasks

            sync_geo_signal_tasks(track.analysis_run)
        except Exception:
            logger.exception("PromptTrack #%d: GEO task resync failed", track.pk)
    except Exception as exc:
        logger.warning("PromptTrack #%d fire failed: %s", track.pk, exc)

def _competitor_host(url: str) -> str:
    """Extract a normalized lookup host from a competitor URL.
    Strips protocol and a leading 'www.' so it can be compared against
    PromptCitation.domain (which is stored the same way)."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        host = urlparse(raw).netloc
    except Exception:
        return ""
    return host.removeprefix("www.").lower()

def _opportunity_service(slug: str, track_id: int):
    """Resolve the run+track and return an OpportunityService bound to it."""
    from django.shortcuts import get_object_or_404

    from ..services.opportunities import OpportunityService

    run = get_object_or_404(AnalysisRun, slug=slug)
    track = get_object_or_404(
        PromptTrack,
        pk=track_id,
        analysis_run=run,
        deleted_at__isnull=True,
    )
    return OpportunityService(track=track)

def _insights_flag(slug: str) -> str:
    return f"overview_insights_generating:{slug}"

def _start_insights_generation(run_id: int, slug: str) -> None:
    """Spawn a daemon thread to (re)generate the overview insight report."""
    import threading

    def _work():
        close_old_connections()
        try:
            from ..services.overview_insights import get_or_generate

            run = AnalysisRun.objects.get(pk=run_id)
            get_or_generate(run, force=True)
        except Exception:
            logger.exception("overview insights generation failed for %s", slug)
        finally:
            from django.core.cache import cache

            cache.delete(_insights_flag(slug))
            close_old_connections()

    threading.Thread(target=_work, daemon=True).start()

def _serialize_product(p):
    return {
        "id": p.id,
        "provider": p.provider.slug,
        "provider_name": p.provider.display_name,
        "sku": p.sku,
        "domain": p.domain,
        "title": p.title,
        "link_type": p.link_type,
        "domain_authority": p.domain_authority,
        "domain_rank": p.domain_rank,
        "monthly_traffic": p.monthly_traffic,
        "niche_tags": p.niche_tags or [],
        "language": p.language,
        "country": p.country,
        "do_follow": p.do_follow,
        "price_cents": p.retail_price_cents,
        "currency": p.currency,
        "lead_time_days": p.lead_time_days,
    }

def _serialize_order(o):
    return {
        "id": o.id,
        "status": o.status,
        "provider": o.provider.slug,
        "provider_name": o.provider.display_name,
        "domain": o.product.domain,
        "title": o.product.title,
        "target_url": o.target_url,
        "anchor_text": o.anchor_text,
        "price_cents": o.price_cents,
        "currency": o.currency,
        "proof_url": o.proof_url,
        "error_message": o.error_message,
        "prompt_track_id": o.prompt_track_id,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "ordered_at": o.ordered_at.isoformat() if o.ordered_at else None,
        "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
    }

def _serialize_content_suggestion(s):
    return {
        "id": s.id,
        "title": s.title,
        "rationale": s.rationale,
        "target_field": s.target_field,
        "current_excerpt": s.current_excerpt,
        "proposed_value": s.proposed_value,
        "status": s.status,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }

def _resolve_shopify_integration_for_run(run):
    """Return the active Shopify Integration tied to the run's organization,
    or (None, error_response) if there isn't one."""
    if not run.organization_id:
        return None, Response({"detail": "Run is not linked to an organization."}, status=400)
    from apps.integrations.models import Integration

    integ = Integration.objects.filter(
        organization_id=run.organization_id,
        provider=Integration.Provider.SHOPIFY,
        is_active=True,
    ).first()
    if not integ:
        return None, Response(
            {"detail": "No connected Shopify integration for this run's store."},
            status=503,
        )
    return integ, None

def _sentiment_case():
    """Map PromptResult.sentiment (label) → numeric -1..1 for DB-side averaging."""
    from django.db.models import Case, FloatField, Value, When

    return Case(
        When(sentiment=PromptResult.Sentiment.POSITIVE, then=Value(1.0)),
        When(sentiment=PromptResult.Sentiment.NEGATIVE, then=Value(-1.0)),
        default=Value(0.0),
        output_field=FloatField(),
    )

def _norm_domain(d: str) -> str:
    return (d or "").strip().lower().removeprefix("www.")

def _crawler_ingest_signer():
    from django.core import signing

    return signing.Signer(salt="crawler-ingest")

def crawler_ingest_token(org_id: int) -> str:
    """Org-scoped token the site's log integration uses to report crawler hits."""
    return _crawler_ingest_signer().sign(str(org_id))

def _budget_denied(run):
    """Refuse billable work for an account over its LLM allowance.

    These endpoints spend real money per call and are reachable with only a run
    slug, so without this the budget fuse on ``start_analysis_task`` can be
    walked around by hitting them directly. Fails open if the meter itself
    errors - see services.llm_spend.
    """
    try:
        from ..services.llm_spend import check_budget

        status_ = check_budget(run.email)
    except Exception:
        logger.exception("Budget lookup failed for %s; allowing", run.email)
        return None
    if status_.allowed:
        return None
    return Response(
        {
            "detail": (
                "This account has reached its monthly AI usage allowance. "
                "It resets on a rolling 30-day basis."
            )
        },
        status=status.HTTP_402_PAYMENT_REQUIRED,
    )

def _acting_owner_email(email: str) -> str:
    """The brand-owner identity ``email`` acts as.

    An agency teammate legitimately manages their agency's brands, so ownership
    resolves through ``get_agency_context`` exactly as ``ScheduleView._owned_org``
    does. Comparing the caller's own address against ``owner_email`` directly
    would lock those teammates out of brands they are paid to work on.
    """
    from apps.accounts.agency_utils import get_agency_context

    normalized = (email or "").strip().lower()
    ctx = get_agency_context(normalized)
    return (ctx.agency_email if ctx else normalized).strip().lower()

def _may_access_run(email: str, run) -> bool:
    """Does ``email`` own the brand ``run`` belongs to?

    Derived entirely from server records: the caller supplies no organization or
    owner id, only a verified address that is mapped to an owner here.
    """
    if not email:
        return False
    acting = _acting_owner_email(email)
    org = getattr(run, "organization", None)
    # Runs predating organizations are owned by the address that started them.
    owner = (org.owner_email if org is not None else run.email) or ""
    return owner.strip().lower() == acting

def _unauthenticated(action: str):
    """Refusal for a request carrying no verified identity.

    Distinguishes "you did not authenticate" from "this deployment cannot
    authenticate anyone". Both refuse, but a bare 401 on a server with no JWKS
    configured sends the operator hunting for a bad token that does not exist.
    """
    from django.conf import settings as _settings

    if not getattr(_settings, "BETTER_AUTH_JWKS_URL", ""):
        logger.error(
            "Refusing %s: BETTER_AUTH_JWKS_URL is unset, so no caller can "
            "authenticate. Set it to enable this endpoint.",
            action,
        )
        return Response(
            {"detail": "Authentication is not configured on this server."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response({"detail": "Authentication required."}, status=status.HTTP_401_UNAUTHORIZED)

def _scoped_run(request, slug: str):
    """Load a run by slug, scoped to the caller's identity when there is one.

    Returns ``(run, error_response)``; callers do
    ``run, err = _scoped_run(request, slug); if err: return err``.

    A run slug is unguessable but it is not a credential: it travels in browser
    history, support tickets and analytics tooling. The goal is that holding one
    never grants a third party the brand's data.

    Three cases, in order:

    1. **Verified caller** - identity comes from ``request.user``, which
       ``BetterAuthJWTAuthentication`` sets only after verifying a signed JWT.
       Ownership is re-derived from server records and a mismatch is 404, not
       403: a 403 confirms the run exists to someone who should not know that.
    2. **No identity and ``REQUIRE_VERIFIED_IDENTITY`` is on** - refused.
    3. **No identity and the flag is off** - the slug admits, which is the
       convention the other ~78 run-scoped endpoints already follow.

    Case 3 is a deliberate, temporary concession and the reason is worth stating
    plainly. This gate previously failed closed for everyone. Shipped against a
    deployment where ``BETTER_AUTH_JWKS_URL`` is unset *and* the frontend sends
    cookies rather than a Bearer token, that meant **no caller could ever
    authenticate**, and six features returned 503 to every user. Failing closed
    on an authentication path that does not exist yet is not a security control,
    it is an outage.

    Case 1 is the part that is kept, and it is not nothing: a signed-in customer
    can no longer read another brand's run by pasting its slug. Flipping
    ``REQUIRE_VERIFIED_IDENTITY`` closes case 3 for every endpoint here at once,
    which is the intended end state once better-auth's JWT plugin is enabled and
    the frontend attaches the token.
    """
    from django.conf import settings as _settings
    from django.shortcuts import get_object_or_404

    from core.auth.identity import verified_email

    email = verified_email(request)
    if not email and getattr(_settings, "REQUIRE_VERIFIED_IDENTITY", False):
        # Refused before the lookup, so an unauthenticated caller cannot tell a
        # real slug (401) from a made-up one (404).
        return None, _unauthenticated(f"{request.method} {request.path}")

    run = get_object_or_404(AnalysisRun, slug=slug)
    if email and not _may_access_run(email, run):
        return None, Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
    return run, None

def _shopify_integration_for(org):
    """The org's active Shopify integration, or None."""
    if org is None:
        return None
    return Integration.objects.filter(
        organization=org, provider=Integration.Provider.SHOPIFY, is_active=True
    ).first()

def _normalize_site(raw: str) -> str:
    """Accept either a BlogPost.site value (market_trends) or its S3 folder
    name (market-trends) and return the canonical site value."""
    from . import blog_store

    folder_to_site = {v: k for k, v in blog_store.SITE_FOLDERS.items()}
    return folder_to_site.get(raw, raw)

