"""
Auto-fix orchestrator — uses AI to apply ANY recommendation to connected stores.
Every recommendation is fixable. The LLM reads the recommendation, fetches the
current page content, generates the fix, and pushes it back.
"""
import logging
import os
import time

import requests

from .models import AutoFixJob, Recommendation

logger = logging.getLogger("apps")

# ALL categories are fixable
FIXABLE_CATEGORIES = {"schema", "technical", "content", "eeat", "entity", "ai_visibility"}

# LLM refusal phrases — if output starts with these, reject it
_REFUSAL_PHRASES = [
    "i cannot", "i can't", "not appropriate", "i notice", "i'm unable",
    "as an ai", "i apologize", "unfortunately", "i'm sorry", "instead of",
]


def _sanitize_llm_output(text: str, purpose: str = "content") -> tuple[str, str | None]:
    """Clean and validate LLM output. Returns (cleaned_text, error_or_none)."""
    if not text or not text.strip():
        return "", "AI returned empty content."

    cleaned = text.strip()

    # Strip markdown code block wrappers robustly
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Find first and last ``` lines
        start = 0
        end = len(lines)
        for i, line in enumerate(lines):
            if line.strip().startswith("```"):
                start = i + 1
                break
        for i in range(len(lines) - 1, start - 1, -1):
            if lines[i].strip().startswith("```"):
                end = i
                break
        cleaned = "\n".join(lines[start:end]).strip()

    if not cleaned:
        return "", "AI output was empty after cleanup."

    # Check for LLM refusals
    lower = cleaned[:500].lower()
    for phrase in _REFUSAL_PHRASES:
        if lower.startswith(phrase):
            return "", f"AI declined: {cleaned[:100]}..."

    # For schema: validate JSON
    if purpose == "schema":
        import json as _json
        import re as _re
        json_match = _re.search(r'\{.*\}', cleaned, _re.DOTALL)
        if json_match:
            try:
                _json.loads(json_match.group())
            except _json.JSONDecodeError as e:
                return "", f"Generated schema has invalid JSON: {e}"

    return cleaned, None


def _detect_fix_type(recommendation: Recommendation) -> str:
    """Determine the fix approach. Returns plugin-compatible fix_type values."""
    title_lower = (recommendation.title or "").lower()
    cat_lower = (recommendation.category or "").lower()
    desc_lower = (recommendation.description or "").lower()

    # File creation
    if "llms.txt" in title_lower:
        return "llms"
    if "robots.txt" in title_lower:
        return "robots"

    # Schema / structured data
    if cat_lower == "schema" or "json-ld" in title_lower or "structured data" in title_lower:
        return "schema"

    # Meta tags (SEO title, meta description)
    if any(kw in title_lower for kw in ("meta description", "seo title", "title tag", "meta title")):
        return "meta"

    # AI meta tags (AI crawler directives)
    if any(kw in title_lower for kw in ("ai meta", "ai-meta", "ai crawler", "ai bot", "gptbot", "claudebot")):
        return "ai_meta"
    if cat_lower == "ai_visibility" and any(kw in desc_lower for kw in ("meta tag", "crawler", "bot")):
        return "ai_meta"

    # Canonical
    if "canonical" in title_lower:
        return "canonical"

    # Viewport
    if "viewport" in title_lower:
        return "viewport"

    # Noindex removal
    if "noindex" in title_lower or "noindex" in desc_lower:
        return "noindex"

    # FAQ sections
    if "faq" in title_lower:
        return "faq"

    # Everything else: enhance page content
    return "content"


# ── LLM ───────────────────────────────────────────────────────────────────

def _call_llm(prompt: str, purpose: str = "auto-fix") -> str:
    """Call LLM via OpenRouter (fallback to Gemini direct)."""
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    google_key = os.getenv("GOOGLE_API_KEY", "")

    t0 = time.time()

    if openrouter_key:
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "google/gemini-2.0-flash-001",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 8192,
                },
                timeout=90,
            )
            if resp.ok:
                text = resp.json()["choices"][0]["message"]["content"].strip()
                duration_ms = int((time.time() - t0) * 1000)
                logger.info("[AUTO-FIX LLM] %s | %dms | %d chars", purpose, duration_ms, len(text))
                return text
            logger.warning("[AUTO-FIX LLM] OpenRouter %d: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("[AUTO-FIX LLM] OpenRouter failed: %s", exc)

    if google_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=google_key)
            model = genai.GenerativeModel("gemini-2.0-flash")
            response = model.generate_content(prompt)
            text = response.text.strip()
            duration_ms = int((time.time() - t0) * 1000)
            logger.info("[AUTO-FIX LLM] %s | %dms | %d chars (gemini)", purpose, duration_ms, len(text))
            return text
        except Exception as exc:
            logger.warning("[AUTO-FIX LLM] Gemini failed: %s", exc)
            raise

    raise ValueError("No LLM API key configured")


# ── Content Fetchers & Pushers ────────────────────────────────────────────

def _shopify_api(integration, path: str, method: str = "GET", payload: dict = None) -> dict:
    shop = integration.metadata.get("shop_domain", "")
    url = f"https://{shop}/admin/api/2026-01/{path}"
    headers = {
        "X-Shopify-Access-Token": integration.get_access_token(),
        "Content-Type": "application/json",
    }
    if method == "GET":
        resp = requests.get(url, headers=headers, timeout=20)
    elif method == "POST":
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
    elif method == "PUT":
        resp = requests.put(url, headers=headers, json=payload, timeout=20)
    else:
        raise ValueError(f"Unsupported method: {method}")
    if not resp.ok:
        if resp.status_code == 401:
            raise ValueError("STORE_AUTH_EXPIRED: Shopify token expired. Reconnect your store in settings.")
        if resp.status_code == 429:
            raise ValueError("RATE_LIMITED: Shopify API rate limited. Try again in a minute.")
        if resp.status_code == 422:
            try:
                errors = resp.json().get("errors", {})
                msg = "; ".join(f"{k}: {v}" for k, v in errors.items()) if isinstance(errors, dict) else str(errors)
                raise ValueError(f"Shopify validation error: {msg}")
            except (ValueError, KeyError):
                pass
        raise ValueError(f"Shopify API {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _wp_auth_headers(integration) -> dict:
    from apps.integrations.services.wordpress import _auth_header, _wpcom_auth_header, _is_wpcom
    if _is_wpcom(integration):
        return _wpcom_auth_header(integration.get_access_token())
    username = integration.metadata.get("username", "")
    return _auth_header(username, integration.get_access_token())


def _fetch_page_content(integration, url: str) -> tuple[dict | None, str]:
    """Fetch current page content from the connected store."""
    provider = integration.provider
    if provider == "shopify":
        return _fetch_shopify_page(integration, url)
    return _fetch_wp_page(integration, url)


def _fetch_shopify_page(integration, url: str) -> tuple[dict | None, str]:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    handle = path.split("/")[-1] if path else ""

    # If URL has a specific path, try to find that page/product by handle
    if handle:
        for resource in ["pages", "products"]:
            try:
                data = _shopify_api(integration, f"{resource}.json?handle={handle}&fields=id,handle,title,body_html")
                items = data.get(resource, [])
                if items:
                    return (
                        {"type": resource, "id": items[0]["id"]},
                        items[0].get("body_html", "") or "",
                    )
            except Exception:
                continue

    # Fallback: try to get any existing page
    try:
        data = _shopify_api(integration, "pages.json?limit=5&fields=id,handle,title,body_html")
        pages = data.get("pages", [])
        if pages:
            # Prefer a page with content
            for p in pages:
                if p.get("body_html", "").strip():
                    return ({"type": "pages", "id": p["id"]}, p.get("body_html", ""))
            return ({"type": "pages", "id": pages[0]["id"]}, pages[0].get("body_html", "") or "")
    except Exception:
        pass

    # Fallback: try first product
    try:
        data = _shopify_api(integration, "products.json?limit=1&fields=id,handle,title,body_html")
        products = data.get("products", [])
        if products:
            return ({"type": "products", "id": products[0]["id"]}, products[0].get("body_html", "") or "")
    except Exception:
        pass

    # Last resort: create a new page so we have something to work with
    try:
        data = _shopify_api(integration, "pages.json", method="POST", payload={
            "page": {"title": "Home", "body_html": "", "published": True}
        })
        page = data.get("page", {})
        if page.get("id"):
            return ({"type": "pages", "id": page["id"]}, "")
    except Exception:
        pass

    return None, ""


def _is_wpcom(integration) -> bool:
    return bool(integration.metadata.get("is_wpcom", False))


def _extract_slug_from_url(url: str) -> str:
    """Extract the page/post slug from a URL, ignoring date-based paths."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return ""  # Homepage
    # WordPress date-based URLs: /2026/03/24/my-post/ → slug = my-post
    parts = path.split("/")
    # Skip numeric parts (year/month/day)
    non_numeric = [p for p in parts if not p.isdigit()]
    return non_numeric[-1] if non_numeric else ""


def _fetch_wp_page(integration, url: str) -> tuple[dict | None, str]:
    from urllib.parse import urlparse

    if _is_wpcom(integration):
        return _fetch_wpcom_page(integration, url)

    from apps.integrations.services.wordpress import _normalize_site_url
    site_url = _normalize_site_url(integration.metadata.get("site_url", ""))
    headers = _wp_auth_headers(integration)
    slug = _extract_slug_from_url(url)

    # If homepage: try the static front page first
    if not slug:
        try:
            resp = requests.get(f"{site_url}/wp-json/wp/v2/pages", headers=headers,
                                params={"slug": "home", "_fields": "id,content,title,link"}, timeout=15)
            if resp.ok and resp.json():
                post = resp.json()[0]
                return ({"type": "pages", "id": post["id"]}, post.get("content", {}).get("rendered", ""))
        except Exception:
            pass
        # Try "front-page" or "sample-page"
        for fallback_slug in ["front-page", "sample-page"]:
            try:
                resp = requests.get(f"{site_url}/wp-json/wp/v2/pages", headers=headers,
                                    params={"slug": fallback_slug, "_fields": "id,content,title,link"}, timeout=15)
                if resp.ok and resp.json():
                    post = resp.json()[0]
                    return ({"type": "pages", "id": post["id"]}, post.get("content", {}).get("rendered", ""))
            except Exception:
                continue

    # Match by slug
    for post_type in ["posts", "pages"]:
        params = {"slug": slug, "_fields": "id,content,title,link"} if slug else {"per_page": 1}
        try:
            resp = requests.get(f"{site_url}/wp-json/wp/v2/{post_type}", headers=headers, params=params, timeout=15)
            if resp.ok and resp.json():
                post = resp.json()[0]
                return ({"type": post_type, "id": post["id"]}, post.get("content", {}).get("rendered", ""))
        except Exception:
            continue
    return None, ""


def _fetch_wpcom_page(integration, url: str) -> tuple[dict | None, str]:
    """Fetch page from WordPress.com API — matches the analyzed URL."""
    blog_id = integration.metadata.get("blog_id", "")
    token = integration.get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    slug = _extract_slug_from_url(url)

    # If we have a slug, search for the matching post/page
    if slug:
        for post_type in ["posts", "pages"]:
            try:
                api_url = f"https://public-api.wordpress.com/rest/v1.1/sites/{blog_id}/{post_type}/"
                resp = requests.get(api_url, headers=headers, params={"search": slug, "number": 5}, timeout=15)
                if resp.ok:
                    data = resp.json()
                    items = data.get(post_type, [])
                    # Find exact slug match
                    for post in items:
                        post_slug = (post.get("slug") or "").lower()
                        if post_slug == slug.lower():
                            return (
                                {"type": post_type, "id": post["ID"], "wpcom": True, "blog_id": blog_id},
                                post.get("content", "") or "",
                            )
                    # If no exact match, take first result
                    if items:
                        post = items[0]
                        return (
                            {"type": post_type, "id": post["ID"], "wpcom": True, "blog_id": blog_id},
                            post.get("content", "") or "",
                        )
            except Exception:
                continue

    # Homepage or no slug match — try the front page
    try:
        # WordPress.com: get the page set as front page
        resp = requests.get(
            f"https://public-api.wordpress.com/rest/v1.1/sites/{blog_id}/pages/",
            headers=headers, params={"number": 10}, timeout=15,
        )
        if resp.ok:
            pages = resp.json().get("pages", [])
            # Look for "home" or "front-page" slug
            for page in pages:
                page_slug = (page.get("slug") or "").lower()
                if page_slug in ("home", "front-page", "homepage"):
                    return (
                        {"type": "pages", "id": page["ID"], "wpcom": True, "blog_id": blog_id},
                        page.get("content", "") or "",
                    )
            # If no homepage found, use the first published page
            if pages:
                return (
                    {"type": "pages", "id": pages[0]["ID"], "wpcom": True, "blog_id": blog_id},
                    pages[0].get("content", "") or "",
                )
    except Exception:
        pass

    # Last resort: get latest post
    try:
        resp = requests.get(
            f"https://public-api.wordpress.com/rest/v1.1/sites/{blog_id}/posts/",
            headers=headers, params={"number": 1}, timeout=15,
        )
        if resp.ok:
            posts = resp.json().get("posts", [])
            if posts:
                return (
                    {"type": "posts", "id": posts[0]["ID"], "wpcom": True, "blog_id": blog_id},
                    posts[0].get("content", "") or "",
                )
    except Exception:
        pass

    # Create a new page if nothing exists
    try:
        resp = requests.post(
            f"https://public-api.wordpress.com/rest/v1.1/sites/{blog_id}/posts/new",
            headers=headers,
            json={"title": "Home", "content": "", "status": "publish", "type": "page"},
            timeout=15,
        )
        if resp.ok:
            post = resp.json()
            return (
                {"type": "pages", "id": post["ID"], "wpcom": True, "blog_id": blog_id},
                "",
            )
    except Exception:
        pass

    return None, ""


def _push_content(integration, page_info: dict, new_content: str) -> dict:
    """Push updated content back to the store and verify."""
    provider = integration.provider
    page_id = page_info.get("id", "?")
    page_type = page_info.get("type", "post")
    content_len = len(new_content)

    if provider == "shopify":
        resource = page_info["type"]
        singular = resource.rstrip("s")
        _shopify_api(integration, f"{resource}/{page_id}.json", method="PUT",
                     payload={singular: {"body_html": new_content}})
        return {"status": "success", "message": f"Content updated on Shopify {singular} (ID: {page_id}, {content_len} chars)."}

    # WordPress.com
    if page_info.get("wpcom"):
        blog_id = page_info["blog_id"]
        token = integration.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        # Push
        resp = requests.post(
            f"https://public-api.wordpress.com/rest/v1.1/sites/{blog_id}/posts/{page_id}",
            headers=headers,
            json={"content": new_content},
            timeout=15,
        )
        if not resp.ok:
            raise ValueError(f"WordPress.com API error: {resp.status_code} {resp.text[:100]}")

        # Verify — re-fetch and check content was actually updated
        try:
            verify = requests.get(
                f"https://public-api.wordpress.com/rest/v1.1/sites/{blog_id}/posts/{page_id}",
                headers=headers, params={"fields": "ID,title,URL,content"}, timeout=10,
            )
            if verify.ok:
                vdata = verify.json()
                verified_len = len(vdata.get("content", ""))
                title = vdata.get("title", "")
                page_url = vdata.get("URL", "")
                logger.info("[AUTO-FIX] Verified: post %s '%s' (%s) — %d chars", page_id, title, page_url, verified_len)
                return {
                    "status": "success",
                    "message": f"Content updated on WordPress — \"{title}\" ({page_url}). {content_len} chars written, {verified_len} chars verified.",
                }
        except Exception as exc:
            logger.warning("[AUTO-FIX] Verification failed: %s", exc)

        return {"status": "success", "message": f"Content updated on WordPress (ID: {page_id}, {content_len} chars). Verification skipped."}

    # Self-hosted WordPress
    from apps.integrations.services.wordpress import _normalize_site_url
    site_url = _normalize_site_url(integration.metadata.get("site_url", ""))
    headers = {**_wp_auth_headers(integration), "Content-Type": "application/json"}
    resp = requests.post(
        f"{site_url}/wp-json/wp/v2/{page_type}/{page_id}",
        headers=headers, json={"content": new_content}, timeout=15
    )
    if resp.ok:
        post_data = resp.json()
        title = post_data.get("title", {}).get("rendered", "")
        link = post_data.get("link", "")
        return {"status": "success", "message": f"Content updated on WordPress — \"{title}\" ({link}). {content_len} chars written."}
    raise ValueError(f"WordPress API error: {resp.status_code}")


# ── Fix Executors ─────────────────────────────────────────────────────────

def _fix_content_enhance(integration, run, recommendation) -> dict:
    """Use AI to apply any content/eeat/entity/visibility recommendation."""
    brand_name = run.brand_name or "the website"
    page_info, content = _fetch_page_content(integration, run.url)

    if not page_info:
        return {"status": "failed", "message": f"Could not find the page at {run.url} in your store."}

    prompt = f"""You are a GEO (Generative Engine Optimization) expert hired to improve a webpage.

TASK: Apply this specific recommendation to the page content below.

RECOMMENDATION TITLE: {recommendation.title}
RECOMMENDATION PRIORITY: {recommendation.priority}
RECOMMENDATION DESCRIPTION: {recommendation.description}
DETAILED INSTRUCTIONS: {recommendation.action}
BRAND NAME: {brand_name}
PAGE URL: {run.url}

CURRENT PAGE CONTENT (HTML):
{content[:10000]}

IMPORTANT RULES:
1. Keep ALL existing content. Do NOT remove anything.
2. ADD the recommended improvements naturally inline within the content.
3. Make additions feel organic, not forced — weave them into existing sections.
4. Use real, verifiable facts, sources, and data when adding citations/statistics.
5. Use proper HTML formatting (links, blockquotes, lists, etc).
6. Return ONLY the complete improved HTML content.
7. Do NOT wrap the output in markdown code blocks (no ```).
8. Do NOT add explanations before or after — just the HTML."""

    raw = _call_llm(prompt, f"enhance-{recommendation.category}")
    enhanced, err = _sanitize_llm_output(raw, "content")
    if err:
        return {"status": "failed", "message": err}

    return _push_content(integration, page_info, enhanced)


def _fix_schema_markup(integration, run, recommendation) -> dict:
    """Generate and inject proper JSON-LD structured data."""
    brand_name = run.brand_name or "the website"
    page_info, content = _fetch_page_content(integration, run.url)

    if not page_info:
        return {"status": "failed", "message": f"Could not find the page at {run.url}."}

    prompt = f"""Generate comprehensive JSON-LD structured data for this website.

BRAND: {brand_name}
URL: {run.url}
PAGE CONTENT (first 3000 chars): {content[:3000]}

Generate valid JSON-LD wrapped in <script type="application/ld+json"> tags. Include:
1. Organization schema (name, url, logo if mentioned)
2. WebSite schema with SearchAction
3. If product page: Product schema
4. If article/blog: Article schema
5. BreadcrumbList if applicable

Return ONLY the <script> tag(s). No markdown, no explanations."""

    raw = _call_llm(prompt, "generate-schema")
    schema_html, err = _sanitize_llm_output(raw, "schema")
    if err:
        return {"status": "failed", "message": err}

    if "<script" not in schema_html:
        schema_html = f'<script type="application/ld+json">\n{schema_html}\n</script>'

    new_content = content + "\n" + schema_html
    return _push_content(integration, page_info, new_content)


def _fix_create_file(integration, run, recommendation) -> dict:
    """Create llms.txt, robots.txt, or similar files."""
    brand_name = run.brand_name or "the website"
    title_lower = (recommendation.title or "").lower()

    if "llms.txt" in title_lower:
        filename = "llms.txt"
        prompt = f"""Generate a comprehensive llms.txt file for this website.

BRAND: {brand_name}
URL: {run.url}
INSTRUCTIONS FROM RECOMMENDATION: {recommendation.action}

Follow this exact format:
# {brand_name}

## About
[Detailed paragraph about what {brand_name} does, their mission, products/services]

## Key Information
- Official website: {run.url}
- [5-8 key facts about the brand and what they offer]

## Products/Services
[Detailed list of main offerings with brief descriptions]

## Why Choose {brand_name}
[3-5 unique selling points]

## Contact
- Website: {run.url}

Return ONLY the llms.txt content. No markdown code blocks."""
    else:
        return {"status": "failed", "message": f"Unknown file type in: {recommendation.title}"}

    raw = _call_llm(prompt, f"generate-{filename}")
    file_content, err = _sanitize_llm_output(raw, "file")
    if err:
        return {"status": "failed", "message": err}

    provider = integration.provider
    slug = filename.replace(".", "-")

    if provider == "shopify":
        try:
            _shopify_api(integration, "pages.json", method="POST", payload={
                "page": {
                    "title": filename,
                    "handle": slug,
                    "body_html": f"<pre style='white-space:pre-wrap;font-family:monospace;'>{file_content}</pre>",
                    "published": True,
                }
            })
            shop = integration.metadata.get("shop_domain", "")
            return {"status": "success", "message": f"{filename} created at https://{shop}/pages/{slug}"}
        except ValueError as e:
            if "422" in str(e):
                try:
                    data = _shopify_api(integration, f"pages.json?handle={slug}&fields=id")
                    pages = data.get("pages", [])
                    if pages:
                        _shopify_api(integration, f"pages/{pages[0]['id']}.json", method="PUT", payload={
                            "page": {"body_html": f"<pre style='white-space:pre-wrap;font-family:monospace;'>{file_content}</pre>"}
                        })
                        shop = integration.metadata.get("shop_domain", "")
                        return {"status": "success", "message": f"{filename} updated at https://{shop}/pages/{slug}"}
                except Exception:
                    pass
            return {"status": "failed", "message": f"Failed to create {filename}: {e}"}
    elif _is_wpcom(integration):
        # WordPress.com — use public API
        blog_id = integration.metadata.get("blog_id", "")
        token = integration.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        site_url = integration.metadata.get("site_url", "")

        try:
            resp = requests.post(
                f"https://public-api.wordpress.com/rest/v1.1/sites/{blog_id}/posts/new",
                headers=headers,
                json={"title": filename, "slug": slug, "content": f"<pre style='white-space:pre-wrap;'>{file_content}</pre>", "status": "publish"},
                timeout=15,
            )
            if resp.ok:
                post_url = resp.json().get("URL", f"{site_url}/{slug}")
                return {"status": "success", "message": f"{filename} created at {post_url}"}
            return {"status": "failed", "message": f"WordPress.com API error: {resp.status_code}"}
        except Exception as e:
            return {"status": "failed", "message": str(e)}
    else:
        # Self-hosted WordPress
        from apps.integrations.services.wordpress import _normalize_site_url
        site_url = _normalize_site_url(integration.metadata.get("site_url", ""))
        headers = {**_wp_auth_headers(integration), "Content-Type": "application/json"}

        resp = requests.get(f"{site_url}/wp-json/wp/v2/pages", headers=headers, params={"slug": slug, "_fields": "id"}, timeout=15)
        if resp.ok and resp.json():
            page_id = resp.json()[0]["id"]
            requests.post(f"{site_url}/wp-json/wp/v2/pages/{page_id}", headers=headers,
                          json={"content": f"<pre>{file_content}</pre>"}, timeout=15)
            return {"status": "success", "message": f"{filename} updated at {site_url}/{slug}"}

        resp = requests.post(f"{site_url}/wp-json/wp/v2/pages", headers=headers,
                             json={"title": filename, "slug": slug, "content": f"<pre>{file_content}</pre>", "status": "publish"}, timeout=15)
        if resp.ok:
            return {"status": "success", "message": f"{filename} created at {site_url}/{slug}"}
        return {"status": "failed", "message": f"WordPress API error: {resp.status_code}"}


# ── Orchestrator ──────────────────────────────────────────────────────────

FIX_EXECUTORS = {
    "content": _fix_content_enhance,
    "faq": _fix_content_enhance,
    "schema": _fix_schema_markup,
    "llms": _fix_create_file,
    "robots": _fix_create_file,
}


def apply_fixes(run, integration, recommendations: list[Recommendation]) -> list[dict]:
    """Apply auto-fixes for a list of recommendations."""
    results = []

    for rec in recommendations:
        fix_type = _detect_fix_type(rec)

        job = AutoFixJob.objects.create(
            analysis_run=run,
            recommendation=rec,
            integration=integration,
            fix_type=fix_type,
            status="running",
        )

        try:
            executor = FIX_EXECUTORS.get(fix_type)
            if not executor:
                result = {"status": "skipped", "message": f"Fix type '{fix_type}' requires the preview/approve flow."}
                job.status = "skipped"
                job.response_data = result
                job.save(update_fields=["status", "response_data"])
                results.append({"recommendation_id": rec.id, "status": "skipped", "message": result["message"], "fix_type": fix_type})
                continue
            result = executor(integration, run, rec)

            job.status = result["status"]
            job.response_data = result
            job.save(update_fields=["status", "response_data"])

            results.append({
                "recommendation_id": rec.id,
                "status": result["status"],
                "message": result["message"],
                "fix_type": fix_type,
            })
        except Exception as e:
            error_str = str(e)
            logger.exception(f"Auto-fix failed for rec {rec.id}")

            # Rate limit backoff — wait and retry once
            if "429" in error_str or "RATE_LIMITED" in error_str:
                logger.warning("Rate limited — waiting 30s before retry")
                time.sleep(30)
                try:
                    result = executor(integration, run, rec)
                    job.status = result["status"]
                    job.response_data = result
                    job.save(update_fields=["status", "response_data"])
                    results.append({
                        "recommendation_id": rec.id,
                        "status": result["status"],
                        "message": result["message"],
                        "fix_type": fix_type,
                    })
                    continue
                except Exception as retry_e:
                    error_str = str(retry_e)
                    logger.warning("Retry also failed: %s", error_str)

            # User-friendly error messages
            if "STORE_AUTH_EXPIRED" in error_str:
                msg = "Store authentication expired. Please reconnect in settings."
            elif "RATE_LIMITED" in error_str:
                msg = "Store API rate limited. Please try again later."
            else:
                msg = error_str

            job.status = "failed"
            job.error_message = msg
            job.save(update_fields=["status", "error_message"])
            results.append({
                "recommendation_id": rec.id,
                "status": "failed",
                "message": msg,
                "fix_type": fix_type,
            })

    return results


# ── Preview + Approve (Plugin-based flow) ─────────────────────────────────

def generate_fix_preview(run, integration, recommendation) -> dict:
    """Generate fix content via LLM and return before/after WITHOUT applying."""
    fix_type = _detect_fix_type(recommendation)
    brand_name = run.brand_name or "the website"

    # Fetch current content
    page_info, current_content = _fetch_page_content(integration, run.url)
    if not page_info:
        return {"status": "error", "message": f"Could not find the page at {run.url}"}

    # Generate fix via LLM
    if fix_type in ("content", "faq"):
        prompt = f"""You are a GEO (Generative Engine Optimization) expert.

TASK: Apply this recommendation to the page content below.

RECOMMENDATION: {recommendation.title}
DESCRIPTION: {recommendation.description}
INSTRUCTIONS: {recommendation.action}
BRAND: {brand_name}

CURRENT CONTENT:
{current_content[:10000]}

RULES:
1. Keep ALL existing content. Do NOT remove anything.
2. ADD improvements naturally inline.
3. Use real, verifiable facts and sources.
4. Return ONLY the improved HTML. No markdown, no explanations."""

        raw = _call_llm(prompt, f"preview-{recommendation.category}")
        enhanced, err = _sanitize_llm_output(raw, "content")
        if err:
            return {"status": "error", "message": err}

        return {
            "status": "preview",
            "fix_type": fix_type,
            "recommendation_id": recommendation.id,
            "recommendation_title": recommendation.title,
            "original": current_content[:5000],
            "preview": enhanced[:5000],
            "full_content": enhanced,
            "target_post_id": page_info.get("id"),
            "target_type": page_info.get("type", "posts"),
        }

    elif fix_type == "schema":
        prompt = f"""Generate JSON-LD structured data for this website.
BRAND: {brand_name}
URL: {run.url}
PAGE CONTENT (first 3000 chars): {current_content[:3000]}

Include Organization, WebSite, and relevant type schemas.
Return ONLY valid JSON-LD wrapped in <script type="application/ld+json"> tags."""

        raw = _call_llm(prompt, "preview-schema")
        schema_html, err = _sanitize_llm_output(raw, "schema")
        if err:
            return {"status": "error", "message": err}

        if "<script" not in schema_html:
            schema_html = f'<script type="application/ld+json">\n{schema_html}\n</script>'

        return {
            "status": "preview",
            "fix_type": "schema",
            "recommendation_id": recommendation.id,
            "recommendation_title": recommendation.title,
            "original": "",
            "preview": schema_html[:5000],
            "full_content": schema_html,
            "target_type": "schema",
        }

    elif fix_type in ("llms", "robots"):
        filename = "llms.txt" if fix_type == "llms" else "robots.txt"
        prompt = f"""Create a comprehensive {filename} file for:
BRAND: {brand_name}
URL: {run.url}
PAGE CONTENT (first 2000 chars): {current_content[:2000]}

Include: About, Key Information, Products/Services, Contact sections with URLs."""

        raw = _call_llm(prompt, f"preview-{filename}")
        file_content, err = _sanitize_llm_output(raw, "file")
        if err:
            return {"status": "error", "message": err}

        return {
            "status": "preview",
            "fix_type": fix_type,
            "recommendation_id": recommendation.id,
            "recommendation_title": recommendation.title,
            "original": "",
            "preview": file_content[:5000],
            "full_content": file_content,
            "target_type": "file",
        }

    elif fix_type == "meta":
        prompt = f"""Generate an SEO-optimized title and meta description for this page.

BRAND: {brand_name}
URL: {run.url}
RECOMMENDATION: {recommendation.title}
INSTRUCTIONS: {recommendation.action}
CURRENT CONTENT (first 3000 chars): {current_content[:3000]}

Return JSON with two fields: {{"seo_title": "...", "seo_description": "..."}}
Title max 60 chars. Description max 160 chars. No markdown."""

        raw = _call_llm(prompt, "preview-meta")
        return {
            "status": "preview",
            "fix_type": "meta",
            "recommendation_id": recommendation.id,
            "recommendation_title": recommendation.title,
            "original": "",
            "preview": raw.strip()[:2000],
            "full_content": raw.strip(),
            "target_type": "meta",
        }

    elif fix_type == "ai_meta":
        ai_bots = ["GPTBot", "Google-Extended", "anthropic-ai", "ClaudeBot", "PerplexityBot", "ChatGPT-User", "CCBot"]
        preview_html = "\n".join(f'<meta name="{bot}" content="index, follow">' for bot in ai_bots)
        return {
            "status": "preview",
            "fix_type": "ai_meta",
            "recommendation_id": recommendation.id,
            "recommendation_title": recommendation.title,
            "original": "",
            "preview": f"AI crawler meta tags will be added:\n\n{preview_html}",
            "full_content": "enabled",
            "target_type": "ai_meta",
        }

    elif fix_type in ("canonical", "viewport", "noindex"):
        # These are simple config fixes — no LLM needed, just tell the plugin
        messages = {
            "canonical": f"Set canonical URL to {run.url}",
            "viewport": "Set responsive viewport meta tag",
            "noindex": "Remove noindex directive and force indexing",
        }
        content_map = {
            "canonical": run.url,
            "viewport": "width=device-width, initial-scale=1",
            "noindex": "index, follow",
        }
        return {
            "status": "preview",
            "fix_type": fix_type,
            "recommendation_id": recommendation.id,
            "recommendation_title": recommendation.title,
            "original": "",
            "preview": messages[fix_type],
            "full_content": content_map[fix_type],
            "target_type": fix_type,
        }

    return {"status": "error", "message": f"Unknown fix type: {fix_type}"}


def apply_approved_fix(run, integration, recommendation, content: str, fix_type: str) -> dict:
    """Apply a user-approved fix — sends to plugin or pushes directly."""
    provider = integration.provider
    site_url = integration.metadata.get("site_url", "")
    api_key = integration.metadata.get("signalor_api_key", "")

    # Try plugin first (WordPress with Signalor plugin installed)
    if provider == "wordpress" and api_key and site_url:
        # Build payload matching plugin's expected fields
        payload = {"fix_type": fix_type, "url": run.url}
        if fix_type in ("content", "faq"):
            payload["content"] = content
        elif fix_type == "schema":
            payload["schema"] = content
        elif fix_type == "llms":
            payload["llms_content"] = content
        elif fix_type == "robots":
            payload["content"] = content
        elif fix_type == "meta":
            # Meta content is JSON: {"seo_title": "...", "seo_description": "..."}
            import json as _json
            try:
                meta = _json.loads(content)
                payload["seo_title"] = meta.get("seo_title", "")
                payload["seo_description"] = meta.get("seo_description", "")
            except (ValueError, TypeError):
                payload["seo_title"] = content
        elif fix_type == "canonical":
            payload["canonical_url"] = content
        elif fix_type in ("viewport", "noindex"):
            payload["content"] = content
        else:
            payload["content"] = content

        try:
            resp = requests.post(
                f"{site_url}/wp-json/signalor/v1/apply-fix",
                headers={"X-Signalor-Key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=20,
            )
            if resp.ok:
                return resp.json()
            logger.warning("Plugin apply-fix failed (%d): %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("Plugin apply-fix error: %s", exc)
        # Fall through to direct API only for non-plugin providers
        logger.info("Plugin failed for %s, falling back to direct API", run.url)

    # Try Shopify app (like WordPress plugin — thin executor on the store)
    if provider == "shopify" and not integration.metadata.get("signalor_app_url"):
        # App URL not configured — check if fixes require the app
        if fix_type in ("schema", "meta", "ai_meta", "noindex"):
            return {
                "status": "failed",
                "message": "Signalor Shopify app not installed. Install it from the Shopify App Store to apply this fix.",
            }

    if provider == "shopify" and integration.metadata.get("signalor_app_url"):
        import json as _json
        import hashlib as _hashlib
        import hmac as _hmac

        app_url = integration.metadata["signalor_app_url"].rstrip("/")
        hmac_secret = integration.metadata.get("signalor_hmac_secret", "")
        shop = integration.metadata.get("shop_domain", "")

        payload = {"fix_type": fix_type, "url": run.url, "shop": shop}
        if fix_type in ("content", "faq"):
            payload["content"] = content
        elif fix_type == "schema":
            payload["schema"] = content
        elif fix_type == "llms":
            payload["llms_content"] = content
        elif fix_type == "meta":
            try:
                meta = _json.loads(content)
                payload["seo_title"] = meta.get("seo_title", "")
                payload["seo_description"] = meta.get("seo_description", "")
            except (ValueError, TypeError):
                payload["seo_title"] = content
        elif fix_type == "ai_meta":
            payload["content"] = content
        else:
            payload["content"] = content

        body = _json.dumps(payload).encode("utf-8")
        signature = _hmac.new(hmac_secret.encode(), body, _hashlib.sha256).hexdigest()

        try:
            resp = requests.post(
                f"{app_url}/api/apply-fix",
                headers={
                    "X-Signalor-Signature": signature,
                    "X-Signalor-Shop": shop,
                    "Content-Type": "application/json",
                },
                data=body,
                timeout=25,
            )
            if resp.ok:
                return resp.json()
            logger.warning("Shopify app apply-fix failed (%d): %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("Shopify app apply-fix error: %s", exc)
        logger.info("Shopify app failed for %s, falling back to direct API", run.url)

    # Fallback: direct API push (existing behavior)
    if fix_type == "llms":
        # llms.txt: create as a new page, don't fetch existing content
        slug_name = "llms-txt"
        if provider == "shopify":
            try:
                _shopify_api(integration, "pages.json", method="POST", payload={
                    "page": {"title": "llms.txt", "handle": slug_name,
                             "body_html": f"<pre style='white-space:pre-wrap;font-family:monospace;'>{content}</pre>",
                             "published": True}
                })
                shop = integration.metadata.get("shop_domain", "")
                return {"status": "success", "message": f"llms.txt created at https://{shop}/pages/{slug_name}"}
            except ValueError:
                return {"status": "failed", "message": "Failed to create llms.txt page on Shopify"}
        elif provider == "wordpress":
            # WordPress.com: create as post
            blog_id = integration.metadata.get("blog_id", "")
            if blog_id:
                token = integration.get_access_token()
                resp = requests.post(
                    f"https://public-api.wordpress.com/rest/v1.1/sites/{blog_id}/posts/new",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"title": "llms.txt", "content": f"<pre>{content}</pre>", "status": "publish"},
                    timeout=15,
                )
                if resp.ok:
                    post = resp.json()
                    return {"status": "success", "message": f"llms.txt created at {post.get('URL', '')}"}
            return {"status": "failed", "message": "Failed to create llms.txt on WordPress"}
        return {"status": "failed", "message": f"Cannot create llms.txt on {provider}"}

    # Content/schema fixes: need page context
    page_info, existing_content = _fetch_page_content(integration, run.url)
    if not page_info:
        return {"status": "failed", "message": f"Could not find page at {run.url}"}

    if fix_type in ("content", "faq"):
        return _push_content(integration, page_info, content)
    elif fix_type == "schema":
        new_content = existing_content + "\n" + content if existing_content else content
        return _push_content(integration, page_info, new_content)

    # meta, canonical, viewport, noindex — plugin-only fixes, no direct API fallback
    return {"status": "failed", "message": f"Fix type '{fix_type}' requires the Signalor plugin. Install it on your WordPress site."}
