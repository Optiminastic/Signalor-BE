"""Satellite blog network — auto-backlinks engine.

Extracted from ``BlogAutoPublishAllView`` so the one-click "Add" button and the
daily scheduler (``run_backlink_schedules`` management command) share ONE
implementation: for a brand's ``AnalysisRun``, generate a themed blog for each
of the 5 satellite sites and publish it to S3, each embedding a backlink to the
brand's domain.

Helpers that live in ``apps.analyzer.views`` (``_generate_blog_draft`` etc.) are
imported lazily inside the functions to avoid a circular import (views imports
this module).
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger("apps")

# Per-site angle for the auto-publish batch (themed title/topic per site).
AUTO_SITE_ANGLE = {
    "research": "an in-depth, first-principles research analysis of {subject}",
    "listicals": "a 'Top picks' listicle / roundup about {subject}",
    "market_trends": "a market-trends and what's-next analysis of {subject}",
    "comparison": "a head-to-head comparison of the leading options for {subject}",
    "step_guide": "a practical step-by-step how-to guide for {subject}",
}


def auto_can_add_today(run) -> bool:
    """Return False if an auto batch already published today for this brand.

    Keeps the manual "Add" button and the daily scheduler idempotent — at most
    one auto batch per brand per calendar day (server TZ).
    """
    from django.utils import timezone

    from apps.analyzer import blog_store
    from apps.analyzer.views import _brand_ref_for_run

    try:
        brand_ref = _brand_ref_for_run(run)
        today = timezone.now().date().isoformat()
        for p in blog_store.list_for_brand(brand_ref):
            if p.get("source") == "auto" and str(p.get("published_at") or "")[:10] == today:
                return False
    except Exception:
        logger.exception("auto_can_add_today check failed for run %s", getattr(run, "slug", "?"))
    return True


def run_auto_backlinks(run) -> dict:
    """Generate + publish one themed blog to each of the 5 satellite sites for
    ``run``'s brand.

    Returns ``{"created": [...], "errors": [<site>...], "skipped": bool}``.
    Idempotent per day: if a batch already ran today for this brand, returns
    early with ``skipped=True`` and creates nothing.
    """
    from django.conf import settings as dj_settings
    from django.utils import timezone

    from apps.analyzer import blog_store
    from apps.analyzer.models import BlogPost
    from apps.analyzer.pipeline.citations import host_of
    from apps.analyzer.views import (
        _blog_source_candidates,
        _brand_ref_for_run,
        _generate_blog_draft,
        _short_title,
        _slugify,
        _to_html_from_markdownish,
    )

    if not auto_can_add_today(run):
        return {"created": [], "errors": [], "skipped": True}

    site_url = (run.organization.url if run.organization else "") or run.url or ""
    brand = (
        getattr(run, "brand_name", "")
        or (run.organization.name if run.organization else "")
        or urlparse(site_url).netloc
        or "your brand"
    )
    brand_host = host_of(site_url)
    # Subject: top tracked prompt → else brand AI-search framing.
    try:
        top_prompt = (
            run.prompt_tracks.filter(deleted_at__isnull=True)
            .order_by("-score")
            .values_list("prompt_text", flat=True)
            .first()
        )
    except Exception:
        top_prompt = None
    subject = (top_prompt or f"{brand} and AI search visibility").strip()
    try:
        recommendations = list(run.recommendations.values_list("title", flat=True)[:8])
    except Exception:
        recommendations = []
    sources = _blog_source_candidates(run)
    brand_ref = _brand_ref_for_run(run)
    ref_url = (run.organization.url if run.organization else "") or run.url or ""

    created, errors = [], []
    for site in dict(BlogPost.Site.choices):
        try:
            angle = AUTO_SITE_ANGLE.get(site, "an article about {subject}").format(subject=subject)
            draft = _generate_blog_draft(
                site_url, angle, [], recommendations, length="short", sources=sources
            )
            title = _short_title(draft.get("title") or f"{brand}: {subject}")[:300]
            content_html = _to_html_from_markdownish(draft.get("content_markdown") or "")
            if brand_host and ref_url and brand_host not in content_html:
                content_html += (
                    f'\n<p>Learn more about {brand} at <a href="{ref_url}">{ref_url}</a>.</p>'
                )
            meta = (draft.get("meta_description") or draft.get("excerpt") or "").strip()

            base_slug = _slugify(draft.get("slug") or title)
            slug_val, n = base_slug, 2
            while blog_store.slug_exists(site, slug_val):
                slug_val = f"{base_slug}-{n}"
                n += 1

            now = timezone.now().isoformat()
            post = {
                "id": blog_store.new_id(),
                "site": site,
                "slug": slug_val,
                "title": title,
                "description": meta[:2000],
                "content_html": content_html,
                "image_url": "",
                "category": "",
                "brand_url": ref_url,
                "brand_ref": brand_ref,
                "source": "auto",
                "status": "published",
                "published_at": now,
                "created_at": now,
            }
            blog_store.put_post(post)
            domain = (dj_settings.SATELLITE_SITES.get(site) or "").rstrip("/")
            created.append(
                {
                    "id": post["id"],
                    "site": site,
                    "category": site,
                    "slug": slug_val,
                    "title": title,
                    "url": f"{domain}/{slug_val}" if domain else "",
                    "brand_url": ref_url,
                    "status": "published",
                    "published_at": now,
                }
            )
        except Exception as exc:
            logger.warning(
                "auto-backlinks: site %s failed for %s: %s", site, getattr(run, "slug", "?"), exc
            )
            errors.append(site)

    return {"created": created, "errors": errors, "skipped": False}
