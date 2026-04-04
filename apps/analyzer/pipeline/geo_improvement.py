"""
GEO SEO Auto-Improvement Service

After an analysis run completes, this service:
1. Reads the pillar scores and findings from the run
2. Generates specific, actionable fixes using an LLM
3. Pushes those fixes back to the connected platform (Shopify or WordPress)
4. Records each applied improvement in the GeoImprovement model
"""
from __future__ import annotations

import json
import logging
from datetime import timezone

from django.utils import timezone as django_timezone

logger = logging.getLogger("apps")


# ─── LLM helper ──────────────────────────────────────────────────────────────

def _llm_generate(prompt: str) -> str:
    """Call the project's LLM (OpenRouter / Gemini) and return the text."""
    import os
    import requests

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set")

    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "anthropic/claude-3-haiku",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 800,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


# ─── Issue extraction ─────────────────────────────────────────────────────────

def _extract_issues(page_score) -> list[dict]:
    """
    Parse the pillar details from a PageScore and return a list of fixable issues.
    Each issue: {pillar, finding, score}
    """
    issues = []

    checks_map = {
        "technical": page_score.technical_details,
        "schema": page_score.schema_details,
        "eeat": page_score.eeat_details,
        "content": page_score.content_details,
    }

    for pillar, details in checks_map.items():
        if not isinstance(details, dict):
            continue
        findings = details.get("findings", [])
        checks = details.get("checks", {})

        for finding in findings:
            issues.append({"pillar": pillar, "finding": finding})

        # Technical pillar: sometimes the scoring populates "checks" but not "findings".
        # Make the fix-plan generation robust by deriving issues from checks too.
        if pillar == "technical" and isinstance(checks, dict):
            if not checks.get("llms_txt"):
                issues.append({"pillar": "technical", "finding": "no_llms_txt"})
            else:
                llms_quality = checks.get("llms_txt_quality")
                if llms_quality in {"minimal", "basic"}:
                    issues.append(
                        {"pillar": "technical", "finding": f"llms_txt_{llms_quality}_content"}
                    )

            if not checks.get("has_sitemap"):
                issues.append({"pillar": "technical", "finding": "no_sitemap"})

            ai_allowed = checks.get("ai_bots_allowed")
            blocked_bots = checks.get("blocked_bots") or []
            if ai_allowed is False or (isinstance(blocked_bots, list) and len(blocked_bots) > 0):
                issues.append({"pillar": "technical", "finding": "ai_bots_blocked"})

            if not checks.get("has_robots_txt"):
                issues.append({"pillar": "technical", "finding": "no_robots_txt"})

        # Flag specific missing GEO signals
        if pillar == "technical":
            if not checks.get("has_hreflang"):
                issues.append({"pillar": "technical", "finding": "missing_hreflang"})
            if not checks.get("has_geo_meta"):
                issues.append({"pillar": "technical", "finding": "missing_geo_meta"})

        if pillar == "schema":
            if not checks.get("has_schema"):
                issues.append({"pillar": "schema", "finding": "missing_schema_markup"})

        if pillar == "content":
            if not checks.get("has_faq"):
                issues.append({"pillar": "content", "finding": "missing_faq"})

    return issues


def _infer_fix_keys(issues: list[dict]) -> set[str]:
    """Map raw issues/findings into normalized fix keys for UI + apply pipeline."""
    keys: set[str] = set()
    for issue in issues:
        finding = str(issue.get("finding", "")).lower()
        pillar = str(issue.get("pillar", "")).lower()
        if "llms" in finding:
            keys.add("llms_txt")
        if "sitemap" in finding:
            keys.add("sitemap")
        if "robots" in finding or "ai_bots_blocked" in finding or "crawl_blocked_403" in finding:
            keys.add("robots_txt")
        if "schema" in finding:
            keys.add("schema_markup")
        if "meta" in finding or "geo_meta" in finding:
            keys.add("meta_tags")
        if "faq" in finding:
            keys.add("faq_content")
        if pillar == "content":
            keys.add("content_quality")
    # Always include baseline metadata/content improvements for connected stores.
    keys.update({"meta_tags", "content_quality"})
    # Always include crawl-file/AI-discovery technical items.
    # This ensures "Technical GEO" is always fully covered in the Fix Plan,
    # even when analysis storage and live curl results differ.
    keys.update({"llms_txt", "sitemap", "robots_txt"})
    return keys


def get_geo_fix_plan(run) -> list[dict]:
    """
    Build a user-visible fix plan from analysis findings.
    Each item: key, title, description, auto_apply.
    """
    from apps.analyzer.models import PageScore

    page_score = PageScore.objects.filter(analysis_run=run).first()
    if not page_score:
        return []
    keys = _infer_fix_keys(_extract_issues(page_score))

    catalog = {
        "meta_tags": {
            "title": "Improve meta titles and descriptions",
            "description": "Update page SEO tags for clearer GEO intent and better AI/search snippet quality.",
            "auto_apply": True,
        },
        "content_quality": {
            "title": "Improve product/page content quality",
            "description": "Expand thin descriptions and improve copy clarity for stronger GEO scoring.",
            "auto_apply": True,
        },
        "schema_markup": {
            "title": "Add organization schema markup",
            "description": "Add JSON-LD schema blocks to strengthen entity understanding.",
            "auto_apply": True,
        },
        "faq_content": {
            "title": "Add FAQ-style supporting content",
            "description": "Add concise Q&A style supporting copy for AI answer extraction.",
            "auto_apply": True,
        },
        "llms_txt": {
            "title": "Publish llms.txt",
            "description": "Add a crawl guidance file for AI agents at /llms.txt.",
            "auto_apply": True,
        },
        "sitemap": {
            "title": "Ensure sitemap availability",
            "description": "Expose and verify sitemap.xml for complete crawl discovery.",
            "auto_apply": True,
        },
        "robots_txt": {
            "title": "Allow AI bots in robots.txt",
            "description": "Update robots.txt so AI crawlers aren’t blocked, improving crawl and indexing reliability.",
            "auto_apply": True,
        },
    }

    plan = []
    for key in sorted(keys):
        if key in catalog:
            plan.append({"key": key, **catalog[key]})
    return plan


def _recommendation_to_fix_key(rec) -> str | None:
    """
    Best-effort mapping from a Recommendation record to one of our supported fix keys.
    Used only for UI labeling (auto_apply vs manual), not for the actual apply logic.
    """
    title = (getattr(rec, "title", "") or "").lower()
    action = (getattr(rec, "action", "") or "").lower()
    category = (getattr(rec, "category", "") or "").lower()

    # Technical crawl files + AI discovery
    if "llms" in title or "llms.txt" in action:
        return "llms_txt"
    if "robots.txt" in action or "robots" in title:
        return "robots_txt"
    if "sitemap.xml" in action or "sitemap" in title:
        return "sitemap"

    # Metadata / structure
    if "json-ld" in action or "structured data" in title or "schema" in title:
        return "schema_markup"
    if "faq" in title or "faq" in action:
        return "faq_content"

    if "canonical" in title or "og:" in title or "meta" in title:
        return "meta_tags"

    # Content improvements bucket
    if "citation" in title or "answer" in action or "friendly structure" in title or "key takeaway" in action:
        return "content_quality"

    # Fallbacks by category
    if category in {"metadata"}:
        return "meta_tags"
    if category in {"content_quality", "trust"}:
        return "content_quality"

    return None


def get_all_recommendations_fix_plan(run) -> list[dict]:
    """
    Convert ALL run recommendations into the same UI-friendly fix plan format.
    """
    from apps.analyzer.models import Recommendation

    # Determine what we can auto-apply (from geo issues + platform capabilities).
    # Apply execution is still done by run_geo_improvements; this flag is UI-only for "auto apply".
    page_score = None
    try:
        from apps.analyzer.models import PageScore
        page_score = PageScore.objects.filter(analysis_run=run).first()
    except Exception:
        page_score = None

    integration_provider = None
    try:
        from apps.integrations.models import Integration

        if run.organization_id:
            integration = Integration.objects.filter(
                organization_id=run.organization_id,
                provider__in=[Integration.Provider.SHOPIFY, Integration.Provider.WORDPRESS],
                is_active=True,
            ).first()
            integration_provider = integration.provider if integration else None
    except Exception:
        integration_provider = None

    technical_auto_apply_supported = integration_provider == "shopify"

    auto_keys = set()
    if page_score:
        auto_keys = _infer_fix_keys(_extract_issues(page_score))

    recs = (
        Recommendation.objects.filter(analysis_run=run)
        .order_by("priority", "pillar", "id")
    )

    plan: list[dict] = []
    for rec in recs:
        fix_key = _recommendation_to_fix_key(rec)
        # FAQ content exists in recommendation generation, but it's not implemented
        # in the platform "apply" pipeline yet. Mark it as manual.
        # Additionally: for WordPress.com/self-hosted we can't reliably create
        # root-level crawl files (/llms.txt, /robots.txt, /sitemap.xml) via our REST API.
        auto_apply = bool(
            fix_key
            and fix_key in auto_keys
            and fix_key != "faq_content"
            and (fix_key not in {"llms_txt", "robots_txt", "sitemap"} or technical_auto_apply_supported)
        )
        plan.append(
            {
                "key": f"rec_{rec.id}",
                "fix_key": fix_key,
                "title": rec.title,
                "description": rec.description,
                "auto_apply": auto_apply,
            }
        )

    # Ensure we don't drop core crawl/file items even if recommendations generation misses them.
    # (UI coverage only.)
    for missing in ["llms_txt", "robots_txt", "sitemap"]:
        catalog_title = {
            "llms_txt": "Publish llms.txt",
            "robots_txt": "Allow AI bots in robots.txt",
            "sitemap": "Ensure sitemap availability",
        }.get(missing, missing)
        catalog_desc = {
            "llms_txt": "Add a crawl guidance file for AI agents at /llms.txt.",
            "robots_txt": "Update robots.txt so AI crawlers aren’t blocked, improving crawl and indexing reliability.",
            "sitemap": "Expose and verify sitemap.xml for complete crawl discovery.",
        }.get(missing, "")
        plan.append(
            {
                "key": f"core_{missing}",
                "fix_key": missing,
                "title": catalog_title,
                "description": catalog_desc,
                "auto_apply": technical_auto_apply_supported,
            }
        )

    # Deduplicate by key (safeguard).
    seen = set()
    deduped = []
    for item in plan:
        if item["key"] in seen:
            continue
        seen.add(item["key"])
        deduped.append(item)
    return deduped


# ─── Fix generation ───────────────────────────────────────────────────────────

def _generate_meta_fix(brand_name: str, site_url: str, current_title: str, current_desc: str) -> dict:
    """Use LLM to produce an improved meta title and description."""
    prompt = f"""You are a GEO SEO expert. Generate an improved meta title and meta description
for this website to improve its geographic search visibility.

Brand: {brand_name}
Site URL: {site_url}
Current meta title: {current_title or '(not set)'}
Current meta description: {current_desc or '(not set)'}

Requirements:
- Meta title: 50-60 characters, include brand name and a geographic signal if relevant
- Meta description: 140-160 characters, compelling, include a geographic keyword naturally
- JSON only, no extra text

Return exactly:
{{"title": "...", "description": "..."}}"""

    try:
        raw = _llm_generate(prompt)
        # Extract JSON from response
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end])
    except Exception as exc:
        logger.warning("Meta fix generation failed: %s", exc)
        return {}


def _generate_schema_markup(brand_name: str, site_url: str, description: str) -> str:
    """Generate JSON-LD Organization schema."""
    prompt = f"""Generate a clean JSON-LD Organization schema markup for this website.

Brand: {brand_name}
URL: {site_url}
Description: {description or 'A business website'}

Return ONLY the JSON-LD script tag, nothing else:
<script type="application/ld+json">
{{...}}
</script>"""

    try:
        return _llm_generate(prompt)
    except Exception as exc:
        logger.warning("Schema generation failed: %s", exc)
        return ""


# ─── Shopify improvements ─────────────────────────────────────────────────────

def _apply_shopify_improvements(run, integration, issues: list[dict]) -> list[dict]:
    """
    Apply GEO SEO improvements to Shopify store pages/products.
    Returns list of improvement dicts.
    """
    import requests as req_lib
    from apps.integrations.services.shopify import normalize_shop_domain, API_VERSION

    shop_domain = integration.metadata.get("shop_domain", "")
    access_token = integration.get_access_token()
    domain = normalize_shop_domain(shop_domain)
    base_url = f"https://{domain}/admin/api/{API_VERSION}"
    headers = {"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"}

    improvements = []
    brand_name = run.brand_name or domain.split(".")[0]

    # 1. Fetch the Shopify store's online store pages
    try:
        pages_resp = req_lib.get(f"{base_url}/pages.json", headers=headers, params={"limit": 10}, timeout=15)
        pages = pages_resp.json().get("pages", []) if pages_resp.ok else []
    except Exception as exc:
        logger.warning("Failed to fetch Shopify pages: %s", exc)
        pages = []

    fix_keys = _infer_fix_keys(issues)

    # 2. For each page, improve meta tags when needed
    for page in pages[:5]:
        page_id = page.get("id")
        current_title = page.get("title", "")
        current_meta_title = page.get("metafields_global_title_tag", "") or current_title
        current_meta_desc = page.get("metafields_global_description_tag", "")

        if "meta_tags" in fix_keys and (not current_meta_desc or len(current_meta_desc) < 80):
            fix = _generate_meta_fix(brand_name, run.url, current_meta_title, current_meta_desc)
            if fix.get("description"):
                try:
                    update_resp = req_lib.put(
                        f"{base_url}/pages/{page_id}.json",
                        headers=headers,
                        json={
                            "page": {
                                "id": page_id,
                                "metafields_global_description_tag": fix["description"],
                                "metafields_global_title_tag": fix.get("title", current_meta_title),
                            }
                        },
                        timeout=15,
                    )
                    if update_resp.ok:
                        improvements.append({
                            "provider": "shopify",
                            "improvement_type": "meta_description",
                            "resource_type": "page",
                            "resource_id": str(page_id),
                            "resource_title": current_title,
                            "field_name": "metafields_global_description_tag",
                            "old_value": current_meta_desc,
                            "new_value": fix["description"],
                            "status": "applied",
                        })
                        logger.info("Applied Shopify page meta description to page %s", page_id)
                    else:
                        improvements.append({
                            "provider": "shopify",
                            "improvement_type": "meta_description",
                            "resource_type": "page",
                            "resource_id": str(page_id),
                            "resource_title": current_title,
                            "field_name": "metafields_global_description_tag",
                            "old_value": current_meta_desc,
                            "new_value": fix["description"],
                            "status": "failed",
                            "error_message": f"HTTP {update_resp.status_code}",
                        })
                except Exception as exc:
                    logger.warning("Failed to update Shopify page %s: %s", page_id, exc)

    # 3. Top products — improve SEO title and description
    try:
        products_resp = req_lib.get(
            f"{base_url}/products.json",
            headers=headers,
            params={"limit": 5, "fields": "id,title,body_html,variants"},
            timeout=15,
        )
        products = products_resp.json().get("products", []) if products_resp.ok else []
    except Exception as exc:
        logger.warning("Failed to fetch Shopify products: %s", exc)
        products = []

    for product in products[:5]:
        product_id = product.get("id")
        product_title = product.get("title", "")
        # Strip HTML from body_html for a plain-text preview
        body_html = product.get("body_html", "") or ""
        plain_desc = body_html[:200].replace("<br>", " ").replace("</p>", " ")

        if "content_quality" in fix_keys and len(plain_desc) < 140:
            # Description is too short — generate an improved one
            prompt = f"""Write a compelling SEO product description for this Shopify product.
Product title: {product_title}
Brand: {brand_name}
Keep it under 200 words, focus on benefits and geographic availability. Plain text only."""
            try:
                new_desc = _llm_generate(prompt)
                update_resp = req_lib.put(
                    f"{base_url}/products/{product_id}.json",
                    headers=headers,
                    json={
                        "product": {
                            "id": product_id,
                            "body_html": f"<p>{new_desc}</p>",
                        }
                    },
                    timeout=15,
                )
                improvements.append({
                    "provider": "shopify",
                    "improvement_type": "content_update",
                    "resource_type": "product",
                    "resource_id": str(product_id),
                    "resource_title": product_title,
                    "field_name": "body_html",
                    "old_value": plain_desc,
                    "new_value": new_desc[:500],
                    "status": "applied" if update_resp.ok else "failed",
                    "error_message": "" if update_resp.ok else f"HTTP {update_resp.status_code}",
                })
            except Exception as exc:
                logger.warning("Product description update failed for %s: %s", product_id, exc)

    # 4. Add lightweight schema markup block to first page body
    if "schema_markup" in fix_keys and pages:
        page = pages[0]
        page_id = page.get("id")
        page_title = page.get("title", "")
        body_html = page.get("body_html", "") or ""
        if "application/ld+json" not in body_html.lower():
            schema_block = _generate_schema_markup(brand_name, run.url, "Shopify storefront")
            if schema_block:
                try:
                    update_resp = req_lib.put(
                        f"{base_url}/pages/{page_id}.json",
                        headers=headers,
                        json={"page": {"id": page_id, "body_html": f"{body_html}\n{schema_block}"}},
                        timeout=15,
                    )
                    improvements.append({
                        "provider": "shopify",
                        "improvement_type": "schema_markup",
                        "resource_type": "page",
                        "resource_id": str(page_id),
                        "resource_title": page_title,
                        "field_name": "body_html",
                        "old_value": "no-json-ld",
                        "new_value": "json-ld-added",
                        "status": "applied" if update_resp.ok else "failed",
                        "error_message": "" if update_resp.ok else f"HTTP {update_resp.status_code}",
                    })
                except Exception as exc:
                    logger.warning("Schema injection failed for Shopify page %s: %s", page_id, exc)

    # 5. Publish technical crawl files (llms.txt, robots.txt, sitemap.xml)
    #
    # Shopify limitation: uploading these as theme assets does NOT make them available at
    # https://{shop}.myshopify.com/llms.txt — the storefront router does not map theme files
    # to the site root. Crawlers succeed only if you also expose the file via App Proxy
    # (e.g. /apps/signalor/llms.txt — see technical.py fallbacks) or another edge route.
    # Filename must be llms.txt (two m's), not llm.txt.
    def _generate_llms_txt(brand_name: str, site_url: str) -> str:
        prompt = f"""You are writing llms.txt for AI crawl guidance.

Brand: {brand_name}
Site URL: {site_url}

Write plain text for /llms.txt (no markdown, no code fences).
Include:
- A short 2-3 sentence site summary
- Key sections/offerings (5-8 bullet lines)
- 4-7 absolute URLs the AI agent should follow (use the Site URL as base)
- Notes for AI agents about crawl behavior (very short)

Requirements:
- Must be at least 400 characters
- Must contain the words "Sitemap" and "Robots"
- Output ONLY the text."""
        return _llm_generate(prompt)

    def _generate_sitemap_xml(site_url: str) -> str:
        base = site_url.rstrip("/")
        # Minimal valid sitemap for faster indexing + easier crawler validation.
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f'  <url><loc>{base}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>\n'
            f'  <url><loc>{base}/llms.txt</loc><changefreq>weekly</changefreq><priority>0.3</priority></url>\n'
            "</urlset>"
        )

    def _generate_robots_txt(site_url: str) -> str:
        base = site_url.rstrip("/")
        # The technical checker only fails when Disallow matches AI bot paths.
        # So we intentionally omit Disallow entirely to allow AI crawlers.
        return f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"

    def _shopify_get_main_theme_id() -> str | None:
        try:
            themes_resp = req_lib.get(
                f"{base_url}/themes.json",
                headers=headers,
                params={"limit": 10, "fields": "id,name,role"},
                timeout=15,
            )
            if not themes_resp.ok:
                return None
            themes = themes_resp.json().get("themes", []) if themes_resp.text else []
            for t in themes:
                if t.get("role") == "main":
                    return str(t.get("id"))
            if themes:
                return str(themes[0].get("id"))
        except Exception as exc:
            logger.warning("Failed to fetch Shopify themes: %s", exc)
        return None

    def _shopify_upsert_theme_asset(theme_id: str, asset_key: str, value: str) -> bool:
        try:
            update_resp = req_lib.put(
                f"{base_url}/themes/{theme_id}/assets.json",
                headers=headers,
                json={"asset": {"key": asset_key, "value": value}},
                timeout=15,
            )
            return bool(update_resp.ok)
        except Exception as exc:
            logger.warning("Shopify asset upsert failed for %s: %s", asset_key, exc)
            return False

    tech_needs = any(k in fix_keys for k in {"llms_txt", "sitemap", "robots_txt"})
    if tech_needs:
        theme_id = _shopify_get_main_theme_id()
        if theme_id:
            llms_value = None
            sitemap_value = None
            robots_value = None
            try:
                if "llms_txt" in fix_keys:
                    llms_value = _generate_llms_txt(brand_name, run.url)
                if "sitemap" in fix_keys:
                    sitemap_value = _generate_sitemap_xml(run.url)
                if "robots_txt" in fix_keys:
                    robots_value = _generate_robots_txt(run.url)
            except Exception as exc:
                logger.warning("Failed to generate technical crawl files: %s", exc)

            asset_key_candidates = {
                "llms_txt": ["llms.txt", "assets/llms.txt", ".well-known/llms.txt"],
                "sitemap": ["sitemap.xml", "assets/sitemap.xml"],
                "robots_txt": ["robots.txt", "assets/robots.txt"],
            }

            for key, value in [
                ("llms_txt", llms_value),
                ("sitemap", sitemap_value),
                ("robots_txt", robots_value),
            ]:
                if not value or key not in fix_keys:
                    continue

                applied = False
                for asset_key in asset_key_candidates.get(key, [key]):
                    if _shopify_upsert_theme_asset(theme_id, asset_key, value):
                        improvements.append(
                            {
                                "provider": "shopify",
                                "improvement_type": key,
                                "resource_type": "file",
                                "resource_id": asset_key,
                                "resource_title": asset_key,
                                "field_name": "content",
                                "old_value": "",
                                "new_value": value[:500],
                                "status": "applied",
                                "error_message": "",
                            }
                        )
                        applied = True
                        break

                if not applied:
                    improvements.append(
                        {
                            "provider": "shopify",
                            "improvement_type": key,
                            "resource_type": "file",
                            "resource_id": key,
                            "resource_title": key,
                            "field_name": "content",
                            "old_value": "",
                            "new_value": value[:500] if value else "",
                            "status": "failed",
                            "error_message": "Failed to publish Shopify theme asset for this crawl file.",
                        }
                    )

    return improvements


# ─── WordPress improvements ───────────────────────────────────────────────────

def _apply_wordpress_improvements(run, integration, issues: list[dict]) -> list[dict]:
    """
    Apply GEO SEO improvements to WordPress posts and pages.
    Branches between WordPress.com OAuth (public-api.wordpress.com REST API)
    and self-hosted WordPress (wp-json/wp/v2 + Application Passwords).
    """
    import requests as req_lib
    from urllib.parse import urljoin
    import base64

    site_url = integration.metadata.get("site_url", "").rstrip("/")
    username = integration.metadata.get("username", "")
    app_password = integration.get_access_token()
    auth_type = integration.metadata.get("auth_type", "")
    is_wpcom = auth_type == "wpcom_oauth"

    improvements = []
    brand_name = run.brand_name or site_url.split("//")[-1].split(".")[0]

    # Root-level static files (/llms.txt, /robots.txt, /sitemap.xml) cannot be
    # reliably served at those paths through the WordPress REST API, so we skip them.
    fix_keys = _infer_fix_keys(issues)
    fix_keys.discard("llms_txt")
    fix_keys.discard("robots_txt")
    fix_keys.discard("sitemap")

    if is_wpcom:
        # ── WordPress.com OAuth → public-api.wordpress.com/rest/v1.1 ──────────
        domain = site_url.replace("https://", "").replace("http://", "").rstrip("/")
        base = f"https://public-api.wordpress.com/rest/v1.1/sites/{domain}"
        headers = {"Authorization": f"Bearer {app_password}", "Content-Type": "application/json"}

        # Fetch pages
        try:
            pages_resp = req_lib.get(
                f"{base}/posts",
                headers=headers,
                params={"type": "page", "number": 5, "status": "publish"},
                timeout=15,
            )
            pages = pages_resp.json().get("posts", []) if pages_resp.ok else []
        except Exception as exc:
            logger.warning("Failed to fetch WPcom pages: %s", exc)
            pages = []

        # Update page excerpts (meta description improvement)
        for page in pages[:3]:
            page_id = page.get("ID")
            title = page.get("title", "")
            excerpt = page.get("excerpt", "") or ""
            if "meta_tags" in fix_keys and len(excerpt.strip()) < 30:
                fix = _generate_meta_fix(brand_name, run.url, title, excerpt)
                if fix.get("description"):
                    try:
                        r = req_lib.post(
                            f"{base}/posts/{page_id}",
                            headers=headers,
                            json={"excerpt": fix["description"]},
                            timeout=15,
                        )
                        improvements.append({
                            "provider": "wordpress",
                            "improvement_type": "meta_description",
                            "resource_type": "page",
                            "resource_id": str(page_id),
                            "resource_title": title,
                            "field_name": "excerpt",
                            "old_value": excerpt,
                            "new_value": fix["description"],
                            "status": "applied" if r.ok else "failed",
                            "error_message": "" if r.ok else f"HTTP {r.status_code}",
                        })
                    except Exception as exc:
                        logger.warning("WPcom page excerpt update failed %s: %s", page_id, exc)

        # Inject schema markup into the first page body
        if "schema_markup" in fix_keys and pages:
            page = pages[0]
            page_id = page.get("ID")
            page_title = page.get("title", "")
            content = page.get("content", "") or ""
            if "application/ld+json" not in content.lower():
                schema_block = _generate_schema_markup(brand_name, run.url, "WordPress website")
                if schema_block:
                    try:
                        r = req_lib.post(
                            f"{base}/posts/{page_id}",
                            headers=headers,
                            json={"content": f"{content}\n{schema_block}"},
                            timeout=15,
                        )
                        improvements.append({
                            "provider": "wordpress",
                            "improvement_type": "schema_markup",
                            "resource_type": "page",
                            "resource_id": str(page_id),
                            "resource_title": page_title,
                            "field_name": "content",
                            "old_value": "no-json-ld",
                            "new_value": "json-ld-added",
                            "status": "applied" if r.ok else "failed",
                            "error_message": "" if r.ok else f"HTTP {r.status_code}",
                        })
                    except Exception as exc:
                        logger.warning("WPcom schema inject failed: %s", exc)

        # Fetch posts and improve short excerpts
        try:
            posts_resp = req_lib.get(
                f"{base}/posts",
                headers=headers,
                params={"type": "post", "number": 3, "status": "publish"},
                timeout=15,
            )
            posts = posts_resp.json().get("posts", []) if posts_resp.ok else []
        except Exception as exc:
            logger.warning("Failed to fetch WPcom posts: %s", exc)
            posts = []

        for post in posts[:2]:
            post_id = post.get("ID")
            title = post.get("title", "")
            excerpt = post.get("excerpt", "") or ""
            if "content_quality" in fix_keys and len(excerpt.strip()) < 30:
                fix = _generate_meta_fix(brand_name, run.url, title, excerpt)
                if fix.get("description"):
                    try:
                        r = req_lib.post(
                            f"{base}/posts/{post_id}",
                            headers=headers,
                            json={"excerpt": fix["description"]},
                            timeout=15,
                        )
                        improvements.append({
                            "provider": "wordpress",
                            "improvement_type": "meta_description",
                            "resource_type": "post",
                            "resource_id": str(post_id),
                            "resource_title": title,
                            "field_name": "excerpt",
                            "old_value": excerpt,
                            "new_value": fix["description"],
                            "status": "applied" if r.ok else "failed",
                            "error_message": "" if r.ok else f"HTTP {r.status_code}",
                        })
                    except Exception as exc:
                        logger.warning("WPcom post excerpt update failed %s: %s", post_id, exc)

    else:
        # ── Self-hosted WordPress → wp-json/wp/v2 + Application Passwords ─────
        raw = f"{username}:{app_password}".encode()
        auth_header = {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}

        # Fetch published pages
        try:
            pages_resp = req_lib.get(
                urljoin(site_url + "/", "wp-json/wp/v2/pages"),
                headers=auth_header,
                params={"status": "publish", "per_page": 5, "_fields": "id,title,slug,excerpt"},
                timeout=15,
            )
            pages = pages_resp.json() if pages_resp.ok else []
        except Exception as exc:
            logger.warning("Failed to fetch WP pages: %s", exc)
            pages = []

        for page in pages[:3]:
            page_id = page.get("id")
            title_rendered = (page.get("title") or {}).get("rendered", "")
            excerpt_rendered = (page.get("excerpt") or {}).get("rendered", "")
            if not excerpt_rendered or len(excerpt_rendered) < 30:
                fix = _generate_meta_fix(brand_name, run.url, title_rendered, excerpt_rendered)
                if fix.get("description"):
                    try:
                        r = req_lib.post(
                            urljoin(site_url + "/", f"wp-json/wp/v2/pages/{page_id}"),
                            headers={**auth_header, "Content-Type": "application/json"},
                            json={"excerpt": fix["description"]},
                            timeout=15,
                        )
                        improvements.append({
                            "provider": "wordpress",
                            "improvement_type": "meta_description",
                            "resource_type": "page",
                            "resource_id": str(page_id),
                            "resource_title": title_rendered,
                            "field_name": "excerpt",
                            "old_value": excerpt_rendered,
                            "new_value": fix["description"],
                            "status": "applied" if r.ok else "failed",
                            "error_message": "" if r.ok else f"HTTP {r.status_code}",
                        })
                    except Exception as exc:
                        logger.warning("WP page update failed %s: %s", page_id, exc)

        # Fetch posts
        try:
            posts_resp = req_lib.get(
                urljoin(site_url + "/", "wp-json/wp/v2/posts"),
                headers=auth_header,
                params={"status": "publish", "per_page": 3, "_fields": "id,title,excerpt"},
                timeout=15,
            )
            posts = posts_resp.json() if posts_resp.ok else []
        except Exception as exc:
            logger.warning("Failed to fetch WP posts: %s", exc)
            posts = []

        for post in posts[:2]:
            post_id = post.get("id")
            title_rendered = (post.get("title") or {}).get("rendered", "")
            excerpt_rendered = (post.get("excerpt") or {}).get("rendered", "")
            if not excerpt_rendered or len(excerpt_rendered) < 30:
                fix = _generate_meta_fix(brand_name, run.url, title_rendered, excerpt_rendered)
                if fix.get("description"):
                    try:
                        r = req_lib.post(
                            urljoin(site_url + "/", f"wp-json/wp/v2/posts/{post_id}"),
                            headers={**auth_header, "Content-Type": "application/json"},
                            json={"excerpt": fix["description"]},
                            timeout=15,
                        )
                        improvements.append({
                            "provider": "wordpress",
                            "improvement_type": "meta_description",
                            "resource_type": "post",
                            "resource_id": str(post_id),
                            "resource_title": title_rendered,
                            "field_name": "excerpt",
                            "old_value": excerpt_rendered,
                            "new_value": fix["description"],
                            "status": "applied" if r.ok else "failed",
                            "error_message": "" if r.ok else f"HTTP {r.status_code}",
                        })
                    except Exception as exc:
                        logger.warning("WP post update failed %s: %s", post_id, exc)

    return improvements


# ─── Main entry point ─────────────────────────────────────────────────────────

def run_geo_improvements(run_id: int) -> int:
    """
    Main entry point called after analysis completes.
    Finds the connected Shopify or WordPress integration for this run's org,
    generates and applies GEO SEO improvements, and records them.

    Returns: number of improvements applied.
    """
    from apps.analyzer.models import AnalysisRun, GeoImprovement, PageScore
    from apps.integrations.models import Integration

    try:
        run = AnalysisRun.objects.get(pk=run_id)
    except AnalysisRun.DoesNotExist:
        logger.error("GeoImprovement: AnalysisRun %d not found", run_id)
        return 0

    if not run.organization_id:
        logger.info("GeoImprovement: run %d has no org, skipping", run_id)
        return 0

    from apps.analyzer.integration_resolve import resolve_store_integration_for_run

    integration = resolve_store_integration_for_run(run.organization, run.url or "")

    if not integration:
        logger.info("GeoImprovement: no Shopify/WP integration for run %d", run_id)
        return 0

    # Get the primary page score
    page_score = PageScore.objects.filter(analysis_run=run).first()
    if not page_score:
        logger.info("GeoImprovement: no page score for run %d", run_id)
        return 0

    score_before = run.composite_score or 0.0
    issues = _extract_issues(page_score)
    logger.info("GeoImprovement: run %d has %d issues, provider=%s", run_id, len(issues), integration.provider)

    # Apply platform-specific improvements
    try:
        if integration.provider == Integration.Provider.SHOPIFY:
            raw_improvements = _apply_shopify_improvements(run, integration, issues)
        elif integration.provider == Integration.Provider.WORDPRESS:
            raw_improvements = _apply_wordpress_improvements(run, integration, issues)
        else:
            raw_improvements = []
    except Exception as exc:
        logger.error("GeoImprovement: applying improvements failed for run %d: %s", run_id, exc, exc_info=True)
        return 0

    # Persist each improvement
    now = django_timezone.now()
    count = 0
    for imp in raw_improvements:
        GeoImprovement.objects.create(
            analysis_run=run,
            provider=imp.get("provider", integration.provider),
            improvement_type=imp.get("improvement_type", "content_update"),
            status=imp.get("status", "applied"),
            resource_type=imp.get("resource_type", ""),
            resource_id=imp.get("resource_id", ""),
            resource_title=imp.get("resource_title", ""),
            field_name=imp.get("field_name", ""),
            old_value=imp.get("old_value", ""),
            new_value=imp.get("new_value", ""),
            score_before=score_before,
            error_message=imp.get("error_message", ""),
            applied_at=now if imp.get("status") == "applied" else None,
        )
        if imp.get("status") == "applied":
            count += 1

    logger.info("GeoImprovement: %d/%d improvements applied for run %d", count, len(raw_improvements), run_id)
    return count
