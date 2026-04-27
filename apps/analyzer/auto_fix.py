"""
Auto-fix orchestrator — generates fix content via AI, then routes ALL writes
through the Shopify app or WordPress plugin. The backend NEVER writes directly
to stores.
"""
import hashlib
import hmac
import json
import logging
import os
import time

import requests

from .models import AutoFixJob, Recommendation

logger = logging.getLogger("apps")

FIXABLE_CATEGORIES = {"schema", "technical", "content", "eeat", "entity", "ai_visibility"}

# Plugin/app may return "applied" or "ok"; dashboard expects "success".
_PLUGIN_OK = frozenset({"success", "applied", "ok", "done", "complete", "completed"})


def _normalize_plugin_status(raw: str | None) -> str:
    if not raw:
        return "failed"
    r = str(raw).lower().strip()
    if r in _PLUGIN_OK:
        return "success"
    if r in ("manual", "skipped"):
        return "manual"
    if r == "partial":
        return "partial"
    return "failed"


def _append_shopify_llms_hint(message: str | None, run_url: str) -> str:
    """Shopify storefronts do not serve theme/root llms.txt at /llms.txt by default."""
    base = (message or "").strip()
    hint = (
        "Note: Shopify does not allow root-level /llms.txt. "
        "Your llms.txt is served at /apps/signalor/llms.txt via App Proxy."
    )
    if base:
        return f"{base} {hint}"
    return hint

_REFUSAL_PHRASES = [
    "i cannot", "i can't", "not appropriate", "i notice", "i'm unable",
    "as an ai", "i apologize", "unfortunately", "i'm sorry", "instead of",
]


def _sanitize_llm_output(text: str, purpose: str = "content") -> tuple[str, str | None]:
    """Clean and validate LLM output. Returns (cleaned_text, error_or_none)."""
    if not text or not text.strip():
        return "", "AI returned empty content."

    cleaned = text.strip()

    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
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

    lower = cleaned[:500].lower()
    for phrase in _REFUSAL_PHRASES:
        if lower.startswith(phrase):
            return "", f"AI declined: {cleaned[:100]}..."

    if purpose == "schema":
        import re as _re
        json_match = _re.search(r'\{.*\}', cleaned, _re.DOTALL)
        if json_match:
            try:
                json.loads(json_match.group())
            except json.JSONDecodeError as e:
                return "", f"Generated schema has invalid JSON: {e}"

    return cleaned, None


def _detect_fix_type(recommendation: Recommendation) -> str:
    """Determine the fix approach. Returns plugin-compatible fix_type values."""
    title_lower = (recommendation.title or "").lower()
    cat_lower = (recommendation.category or "").lower()
    desc_lower = (recommendation.description or "").lower()

    # Manual-only items — cannot be auto-fixed
    manual_keywords = [
        "sitemap", "https", "ssl", "page load speed", "page speed",
        "crawler blocked", "403", "too slow to crawl", "timeout",
        "wikipedia", "reddit", "google ai overview",
        "brand into ai", "social profile", "brand website signal",
    ]
    for kw in manual_keywords:
        if kw in title_lower or kw in desc_lower:
            return "manual"

    if "llms.txt" in title_lower:
        return "llms"
    if "robots.txt" in title_lower:
        return "robots"
    if cat_lower == "schema" or "json-ld" in title_lower or "structured data" in title_lower:
        return "schema"
    if any(kw in title_lower for kw in ("meta description", "seo title", "title tag", "meta title")):
        return "meta"
    if any(kw in title_lower for kw in ("ai meta", "ai-meta", "ai crawler", "ai bot", "gptbot", "claudebot")):
        return "ai_meta"
    if cat_lower == "ai_visibility" and any(kw in desc_lower for kw in ("meta tag", "crawler", "bot")):
        return "ai_meta"
    if "canonical" in title_lower:
        return "canonical"
    if "viewport" in title_lower:
        return "viewport"
    if "noindex" in title_lower or "noindex" in desc_lower:
        return "noindex"
    if "faq" in title_lower:
        return "faq"

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


# ── Plugin / App Router ──────────────────────────────────────────────────
# ALL writes go through here — the backend never touches store APIs directly.

def _send_to_plugin(integration, run, fix_type: str, content: str) -> dict:
    """Route fix to the WordPress plugin or Shopify app. Returns result dict."""
    provider = integration.provider

    if provider == "wordpress":
        return _send_to_wp_plugin(integration, run, fix_type, content)
    elif provider == "shopify":
        return _send_to_shopify_app(integration, run, fix_type, content)
    else:
        return {"status": "failed", "message": f"Unknown provider: {provider}. Connect WordPress or Shopify."}


def _build_payload(fix_type: str, url: str, content: str, shop: str = "") -> dict:
    """Build the payload dict matching plugin/app expected fields."""
    payload = {"fix_type": fix_type, "url": url}
    if shop:
        payload["shop"] = shop

    if fix_type in ("content", "faq"):
        payload["content"] = content
    elif fix_type == "schema":
        payload["schema"] = content
    elif fix_type == "llms":
        payload["llms_content"] = content
    elif fix_type == "robots":
        payload["content"] = content
    elif fix_type == "meta":
        try:
            meta = json.loads(content)
            payload["seo_title"] = meta.get("seo_title", "")
            payload["seo_description"] = meta.get("seo_description", "")
        except (ValueError, TypeError):
            payload["seo_title"] = content
    elif fix_type == "canonical":
        payload["canonical_url"] = content
    elif fix_type in ("ai_meta", "viewport", "noindex"):
        payload["content"] = content
    else:
        payload["content"] = content

    return payload


def _send_to_wp_plugin(integration, run, fix_type: str, content: str) -> dict:
    """Send fix to WordPress plugin endpoint."""
    site_url = integration.metadata.get("site_url", "")
    api_key = integration.metadata.get("signalor_api_key", "")

    if not api_key or not site_url:
        return {
            "status": "failed",
            "message": "Signalor WordPress plugin not connected. Install the plugin and add the API key in Settings > Signalor.",
        }

    payload = _build_payload(fix_type, run.url, content)

    try:
        resp = requests.post(
            f"{site_url}/wp-json/signalor/v1/apply-fix",
            headers={"X-Signalor-Key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=25,
        )
        if resp.ok:
            return resp.json()
        logger.warning("WP plugin apply-fix failed (%d): %s", resp.status_code, resp.text[:200])
        return {"status": "failed", "message": f"WordPress plugin returned {resp.status_code}. Check plugin is active."}
    except requests.Timeout:
        return {"status": "failed", "message": "WordPress plugin timed out. Check your site is reachable."}
    except Exception as exc:
        logger.warning("WP plugin error: %s", exc)
        return {"status": "failed", "message": f"Could not reach WordPress plugin: {exc}"}


def _send_to_shopify_app(integration, run, fix_type: str, content: str) -> dict:
    logger.info(f"Integration obj for Shopify: {json.dumps(vars(integration), default=str)}")
    """Send fix to Shopify Remix app endpoint."""
    app_url = integration.metadata.get("signalor_app_url", "")
    hmac_secret = integration.metadata.get("signalor_hmac_secret", "")
    shop = integration.metadata.get("shop_domain", "")

    if not app_url:
        return {
            "status": "failed",
            "message": "Signalor Shopify app not installed. Install it from your Shopify admin to apply fixes.",
        }

    app_url = app_url.rstrip("/")
    payload = _build_payload(fix_type, run.url, content, shop=shop)
    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(hmac_secret.encode(), body, hashlib.sha256).hexdigest()

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
        return {"status": "failed", "message": f"Shopify app returned {resp.status_code}. Check app is installed."}
    except requests.Timeout:
        return {"status": "failed", "message": "Shopify app timed out. Check your app is running."}
    except Exception as exc:
        logger.warning("Shopify app error: %s", exc)
        return {"status": "failed", "message": f"Could not reach Shopify app: {exc}"}


# ── Content Reader (read-only, for LLM prompt context) ───────────────────

def _read_page_content(integration, url: str) -> str:
    """Fetch current page content from the store via plugin/app read endpoint.
    Falls back to crawling the public URL. Returns HTML string."""
    # Try reading from the public URL as a simple fallback
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; SignalorBot/1.0)"
        })
        if resp.ok:
            # Extract body content roughly
            html = resp.text
            import re
            body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
            if body_match:
                return body_match.group(1)[:12000]
            return html[:12000]
    except Exception:
        pass
    return ""


# ── LLM Content Generators ──────────────────────────────────────────────
# Each generator produces the fix content. The plugin/app applies it.

def _generate_content_fix(run, recommendation) -> tuple[str, str | None]:
    """Generate enhanced content via LLM. Returns (content, error)."""
    brand_name = run.brand_name or "the website"
    from .integration_resolve import resolve_store_integration_for_run
    org = run.organization
    integration = resolve_store_integration_for_run(org, run.url) if org else None
    page_content = _read_page_content(integration, run.url) if integration else ""

    prompt = f"""You are a GEO (Generative Engine Optimization) expert improving a webpage.

TASK: Apply this specific recommendation to the page content below.

RECOMMENDATION: {recommendation.title}
DESCRIPTION: {recommendation.description}
INSTRUCTIONS: {recommendation.action}
BRAND: {brand_name}
URL: {run.url}

CURRENT PAGE CONTENT (HTML):
{page_content[:10000]}

RULES:
1. Keep ALL existing content — do NOT remove anything.
2. ADD improvements naturally inline.
3. Use real, verifiable facts only — NO fake statistics or citations.
4. If this is a product page, improve it as a product page.
5. Use proper HTML formatting.
6. Return ONLY the improved HTML content. No markdown, no explanations."""

    raw = _call_llm(prompt, f"fix-{recommendation.category}")
    return _sanitize_llm_output(raw, "content")


def _generate_schema_fix(run, recommendation) -> tuple[str, str | None]:
    """Generate JSON-LD schema. Returns (schema_html, error)."""
    brand_name = run.brand_name or "the website"
    from .integration_resolve import resolve_store_integration_for_run
    org = run.organization
    integration = resolve_store_integration_for_run(org, run.url) if org else None
    page_content = _read_page_content(integration, run.url) if integration else ""

    prompt = f"""Generate comprehensive JSON-LD structured data for this website.

BRAND: {brand_name}
URL: {run.url}
PAGE CONTENT (first 3000 chars): {page_content[:3000]}

Generate valid JSON-LD wrapped in <script type="application/ld+json"> tags. Include:
1. Organization schema (name, url, logo if mentioned)
2. WebSite schema with SearchAction
3. If product page: Product schema
4. If article/blog: Article schema
5. BreadcrumbList if applicable

Return ONLY the <script> tag(s). No markdown, no explanations."""

    raw = _call_llm(prompt, "fix-schema")
    schema_html, err = _sanitize_llm_output(raw, "schema")
    if err:
        return "", err

    if "<script" not in schema_html:
        schema_html = f'<script type="application/ld+json">\n{schema_html}\n</script>'

    return schema_html, None


def _generate_meta_fix(run, recommendation) -> tuple[str, str | None]:
    """Generate SEO title + meta description. Returns (json_str, error)."""
    brand_name = run.brand_name or "the website"
    from .integration_resolve import resolve_store_integration_for_run
    org = run.organization
    integration = resolve_store_integration_for_run(org, run.url) if org else None
    page_content = _read_page_content(integration, run.url) if integration else ""

    prompt = f"""Generate an SEO-optimized title and meta description for this page.

BRAND: {brand_name}
URL: {run.url}
RECOMMENDATION: {recommendation.title}
INSTRUCTIONS: {recommendation.action}
CURRENT CONTENT (first 3000 chars): {page_content[:3000]}

Return ONLY a JSON object: {{"seo_title": "...", "seo_description": "..."}}
Title max 60 chars. Description max 160 chars. No markdown."""

    raw = _call_llm(prompt, "fix-meta")
    cleaned, err = _sanitize_llm_output(raw, "content")
    if err:
        return "", err

    # Validate it's JSON
    try:
        meta = json.loads(cleaned)
        return json.dumps({"seo_title": meta.get("seo_title", ""), "seo_description": meta.get("seo_description", "")}), None
    except (ValueError, TypeError):
        return json.dumps({"seo_title": cleaned[:60], "seo_description": ""}), None


def _generate_llms_txt(run, recommendation) -> tuple[str, str | None]:
    """Generate llms.txt content. Returns (content, error)."""
    brand_name = run.brand_name or "the website"

    prompt = f"""Create an llms.txt file following the llmstxt.org specification.

BRAND: {brand_name}
URL: {run.url}
INSTRUCTIONS: {recommendation.action}

The llms.txt format is Markdown with this EXACT structure:

# {brand_name}

> One sentence describing what this site/business is about.

## Section Name

- [Page Title](https://full-url): Brief description of the page

RULES:
1. Start with H1 (# Brand Name) — exactly one
2. Blockquote (>) with a one-line description right after H1
3. Use H2 (##) for sections: Products, Pages, Info, etc.
4. Each item is a Markdown link with description: - [Title](URL): Description
5. Use REAL URLs from the site (based on the URL pattern)
6. Keep it concise — table of contents, NOT essays
7. Return ONLY the markdown content. No code blocks."""

    raw = _call_llm(prompt, "fix-llms")
    return _sanitize_llm_output(raw, "file")


def _generate_robots_txt(run, recommendation) -> tuple[str, str | None]:
    """Generate robots.txt content. Returns (content, error)."""
    brand_name = run.brand_name or "the website"

    prompt = f"""Create a robots.txt file for:
BRAND: {brand_name}
URL: {run.url}

Include standard rules + allow all AI crawlers (GPTBot, ClaudeBot, Google-Extended, PerplexityBot, ChatGPT-User, CCBot).
Return ONLY the robots.txt content."""

    raw = _call_llm(prompt, "fix-robots")
    return _sanitize_llm_output(raw, "file")


# ── Fix Executor Map ─────────────────────────────────────────────────────
# Each entry: fix_type → function that generates content string.
# "simple" types (ai_meta, canonical, viewport, noindex) need no LLM.

def _generate_fix_content(fix_type: str, run, recommendation) -> tuple[str, str | None]:
    """Generate the fix content for a given type. Returns (content, error)."""
    if fix_type in ("content", "faq"):
        return _generate_content_fix(run, recommendation)
    elif fix_type == "schema":
        return _generate_schema_fix(run, recommendation)
    elif fix_type == "meta":
        return _generate_meta_fix(run, recommendation)
    elif fix_type == "llms":
        return _generate_llms_txt(run, recommendation)
    elif fix_type == "robots":
        return _generate_robots_txt(run, recommendation)
    elif fix_type == "ai_meta":
        return "enabled", None
    elif fix_type == "canonical":
        return run.url, None
    elif fix_type == "viewport":
        return "width=device-width, initial-scale=1", None
    elif fix_type == "noindex":
        return "index, follow", None
    else:
        return "", f"Unknown fix type: {fix_type}"


# ── Orchestrator ─────────────────────────────────────────────────────────

def _get_manual_walkthrough(recommendation: Recommendation, provider: str) -> str:
    """Return a detailed walkthrough for manual-only fixes."""
    title_lower = (recommendation.title or "").lower()
    desc_lower = (recommendation.description or "").lower()
    is_shopify = provider == "shopify"
    is_wp = provider == "wordpress"

    if "sitemap" in title_lower:
        if is_shopify:
            return (
                "Shopify auto-generates your sitemap at /sitemap.xml — no action needed.\n\n"
                "How to verify:\n"
                "1. Visit https://your-store.myshopify.com/sitemap.xml in your browser\n"
                "2. You should see an XML file listing all your pages, products, and collections\n"
                "3. Submit it to Google Search Console: go to google.com/search-console → Sitemaps → paste your sitemap URL\n"
                "4. Also submit to Bing Webmaster Tools for broader AI coverage\n\n"
                "Tip: AI engines use sitemaps to discover and index your pages. Submitting to search consoles ensures faster discovery."
            )
        return (
            "Your sitemap needs to be properly configured.\n\n"
            "How to fix:\n"
            "1. Install an SEO plugin like Yoast SEO or Rank Math if you haven't already\n"
            "2. Go to the plugin's settings → Sitemaps and make sure it's enabled\n"
            "3. Visit https://yoursite.com/sitemap_index.xml to verify it works\n"
            "4. Submit to Google Search Console and Bing Webmaster Tools\n\n"
            "Tip: A properly configured sitemap helps AI crawlers discover all your important pages."
        )

    if "https" in title_lower or "ssl" in title_lower:
        if is_shopify:
            return (
                "All Shopify stores have HTTPS enabled by default — no action needed.\n\n"
                "How to verify:\n"
                "1. Visit your store URL and check for the padlock icon in the browser address bar\n"
                "2. If using a custom domain, go to Shopify Admin → Settings → Domains\n"
                "3. Ensure your custom domain shows 'SSL certificate active'\n\n"
                "Tip: HTTPS is a trust signal for both search engines and AI models."
            )
        return (
            "Your site needs a valid SSL certificate for HTTPS.\n\n"
            "How to fix:\n"
            "1. Contact your hosting provider to enable SSL\n"
            "2. Most hosts offer free SSL via Let's Encrypt — ask them to enable it\n"
            "3. After enabling, redirect all HTTP traffic to HTTPS\n"
            "4. Update your WordPress URL in Settings → General to use https://\n\n"
            "Tip: AI crawlers may skip or downgrade pages served over plain HTTP."
        )

    if "page speed" in title_lower or "page load" in title_lower or "too slow" in title_lower:
        if is_shopify:
            return (
                "Page speed affects both SEO and AI visibility.\n\n"
                "How to improve:\n"
                "1. Go to Shopify Admin → Online Store → Themes → Customize\n"
                "2. Remove unused apps and scripts — each adds load time\n"
                "3. Compress images: use apps like TinyIMG or Crush.pics\n"
                "4. Minimize custom code in theme.liquid\n"
                "5. Use Shopify's built-in lazy loading for images\n"
                "6. Check your speed score at pagespeed.web.dev\n\n"
                "Tip: AI crawlers have timeouts. If your page takes >5 seconds to load, they may skip it entirely."
            )
        return (
            "Page speed affects both SEO and AI visibility.\n\n"
            "How to improve:\n"
            "1. Install a caching plugin like WP Super Cache or W3 Total Cache\n"
            "2. Compress images with ShortPixel or Imagify\n"
            "3. Use a CDN like Cloudflare (free tier available)\n"
            "4. Minimize plugins — deactivate any you don't need\n"
            "5. Check your speed score at pagespeed.web.dev\n\n"
            "Tip: AI crawlers have timeouts. If your page takes >5 seconds to load, they may skip it entirely."
        )

    if "social profile" in title_lower or "brand website signal" in title_lower:
        return (
            "Strengthen your brand's online presence across external platforms.\n\n"
            "How to fix:\n"
            "1. Create or update profiles on LinkedIn, Twitter/X, Instagram, and Facebook\n"
            "2. Use consistent brand name, logo, and description across all platforms\n"
            "3. Link back to your website from every profile\n"
            "4. Add your social links to your website footer\n"
            "5. If applicable, create a Wikipedia page or get mentioned on industry authority sites\n\n"
            "Tip: AI models cross-reference brand mentions across the web. "
            "The more consistent, authoritative profiles you have, the more confidently AI will recommend you."
        )

    if "wikipedia" in title_lower or "reddit" in title_lower:
        return (
            "Get your brand mentioned on high-authority external platforms.\n\n"
            "How to fix:\n"
            "1. Participate authentically in relevant Reddit communities (don't spam)\n"
            "2. Get featured in industry blogs, podcasts, or review sites\n"
            "3. Contribute expert answers on Quora related to your niche\n"
            "4. If your brand is notable enough, create a Wikipedia article (strict notability rules apply)\n\n"
            "Tip: AI models heavily weight mentions from trusted, high-authority sources. "
            "A single mention on a .edu, .gov, or major publication can significantly boost your AI visibility."
        )

    if "google ai overview" in title_lower or "brand into ai" in title_lower:
        return (
            "Optimize your content to appear in AI-generated overviews and answers.\n\n"
            "How to fix:\n"
            "1. Structure content with clear headings (H2, H3) that match common questions\n"
            "2. Write concise, factual answers in the first 2-3 sentences of each section\n"
            "3. Use comparison tables, numbered lists, and specific data points\n"
            "4. Add FAQ sections with direct, authoritative answers\n"
            "5. Include statistics, case studies, and expert opinions with citations\n"
            "6. Make sure your About page clearly states who you are, what you do, and why you're credible\n\n"
            "Tip: AI overview answers are pulled from content that is structured, specific, and authoritative. "
            "Generic marketing copy is rarely cited."
        )

    if "crawler blocked" in title_lower or "403" in title_lower:
        if is_shopify:
            return (
                "AI crawlers may be blocked from accessing your store.\n\n"
                "How to fix:\n"
                "1. Check if your store has a password — go to Shopify Admin → Online Store → Preferences\n"
                "2. If 'Password protection' is enabled, disable it (or use Signalor's storefront password feature)\n"
                "3. Check your robots.txt at your-store.myshopify.com/robots.txt\n"
                "4. Make sure AI bots (GPTBot, ClaudeBot, PerplexityBot) are not blocked\n"
                "5. Use the Auto Fix for robots.txt to add allow rules for AI crawlers\n\n"
                "Tip: Many stores accidentally block AI crawlers. If they can't access your page, you're invisible to AI."
            )
        return (
            "AI crawlers may be blocked from accessing your site.\n\n"
            "How to fix:\n"
            "1. Check your robots.txt file at yoursite.com/robots.txt\n"
            "2. Make sure AI bots are not blocked (GPTBot, ClaudeBot, PerplexityBot, Google-Extended)\n"
            "3. Check your hosting firewall or security plugin — they may block unknown bots\n"
            "4. If using Cloudflare, check Firewall Rules for bot blocks\n"
            "5. Test by visiting your site with a User-Agent switcher set to 'GPTBot'\n\n"
            "Tip: If AI crawlers get a 403, your page is completely invisible to AI engines."
        )

    # Generic fallback
    return (
        "This fix requires manual action that cannot be automated.\n\n"
        "Review the recommendation details above and follow the guidance. "
        "If you need help, use the AI Assistant chat in your dashboard for step-by-step support."
    )


def _build_homepage_manual_guide(fix_type: str, generated: str, gen_err: str | None, run) -> str:
    """Build a copy-paste ready manual guide with AI-generated content for Shopify homepage fixes."""
    brand = run.brand_name or run.url or "your brand"

    if fix_type == "meta":
        steps = (
            "WHERE TO PASTE:\n"
            "Shopify Admin → Online Store → Preferences\n\n"
            "STEPS:\n"
            "1. Open your Shopify Admin\n"
            "2. Go to Online Store → Preferences\n"
            "3. Find 'Homepage title' and paste the title below\n"
            "4. Find 'Homepage meta description' and paste the description below\n"
            "5. Click Save"
        )
        if gen_err:
            return f"{steps}\n\nCould not generate content: {gen_err}"
        # Parse generated content for title/description
        title_line = ""
        desc_line = ""
        if generated:
            try:
                parsed = json.loads(generated)
                title_line = parsed.get("seo_title", "")
                desc_line = parsed.get("seo_description", "")
            except (json.JSONDecodeError, AttributeError):
                # Plain text — first line is title, rest is description
                lines = [l.strip() for l in generated.strip().split("\n") if l.strip()]
                title_line = lines[0] if lines else ""
                desc_line = lines[1] if len(lines) > 1 else ""

        content_block = ""
        if title_line:
            content_block += f"\nHOMEPAGE TITLE (copy this):\n{title_line}\n"
        if desc_line:
            content_block += f"\nMETA DESCRIPTION (copy this):\n{desc_line}\n"
        if not content_block:
            content_block = f"\n{generated}\n"
        return f"{steps}\n{content_block}"

    if fix_type == "schema":
        steps = (
            "WHERE TO PASTE:\n"
            "Shopify Admin → Online Store → Themes → Customize → App embeds\n\n"
            "STEPS:\n"
            "1. Open your Shopify Admin\n"
            "2. Go to Online Store → Themes → Customize\n"
            "3. Click the App embeds icon (paint brush) in the left sidebar\n"
            "4. Toggle ON both 'Signalor Schema' and 'Signalor AI Meta'\n"
            "5. Click Save\n\n"
            "The Signalor extension auto-injects Organization schema on your homepage.\n"
            "If you want custom schema, add a 'Custom Liquid' section and paste the JSON-LD below."
        )
        if gen_err:
            return f"{steps}\n\nCould not generate schema: {gen_err}"
        return f"{steps}\n\nGENERATED SCHEMA (copy this into Custom Liquid if needed):\n\n<script type=\"application/ld+json\">\n{generated}\n</script>"

    if fix_type == "content":
        steps = (
            "WHERE TO PASTE:\n"
            "Shopify Admin → Online Store → Themes → Customize\n\n"
            "STEPS:\n"
            "1. Open your Shopify Admin\n"
            "2. Go to Online Store → Themes → Customize\n"
            "3. Click 'Add section' and choose 'Rich text' or 'Custom Liquid'\n"
            "4. Paste the content below into the section\n"
            "5. Click Save"
        )
        if gen_err:
            return f"{steps}\n\nCould not generate content: {gen_err}"
        return f"{steps}\n\nCONTENT TO ADD (copy this):\n\n{generated}"

    if fix_type == "faq":
        steps = (
            "WHERE TO PASTE:\n"
            "Shopify Admin → Online Store → Themes → Customize\n\n"
            "STEPS:\n"
            "1. Open your Shopify Admin\n"
            "2. Go to Online Store → Themes → Customize\n"
            "3. Click 'Add section' and choose 'Collapsible content' or 'FAQ'\n"
            "4. Add each question and answer from below\n"
            "5. Click Save"
        )
        if gen_err:
            return f"{steps}\n\nCould not generate FAQ: {gen_err}"
        return f"{steps}\n\nFAQ CONTENT (add each Q&A):\n\n{generated}"

    # Fallback
    if gen_err:
        return f"Could not generate fix: {gen_err}"
    return f"Apply this manually:\n\n{generated}"


def _is_homepage_url(url: str) -> bool:
    """Check if URL is a store homepage (no /pages/ or /products/ path)."""
    try:
        from urllib.parse import urlparse
        path = urlparse(url).path.rstrip("/")
        return not path or path == ""
    except Exception:
        return False

# Fix types that cannot be applied to Shopify homepages (content lives in theme)
_HOMEPAGE_MANUAL_FIX_TYPES = {"content", "faq", "meta", "schema"}


def apply_fixes(run, integration, recommendations: list[Recommendation]) -> list[dict]:
    """Generate fixes via AI + push through plugin/app for each recommendation."""
    results = []
    is_homepage = _is_homepage_url(run.url or "")
    is_shopify = integration.provider == "shopify"

    for rec in recommendations:
        fix_type = _detect_fix_type(rec)

        job = AutoFixJob.objects.create(
            analysis_run=run,
            recommendation=rec,
            integration=integration,
            fix_type=fix_type,
            status="running",
        )

        # Shopify homepage: content/meta/schema/faq — generate content + give copy-paste instructions
        if is_shopify and is_homepage and fix_type in _HOMEPAGE_MANUAL_FIX_TYPES:
            try:
                generated, gen_err = _generate_fix_content(fix_type, run, rec)
            except Exception as e:
                generated, gen_err = "", str(e)

            walkthrough = _build_homepage_manual_guide(fix_type, generated, gen_err, run)
            result = {
                "status": "manual",
                "message": walkthrough,
                "generated_content": generated if not gen_err else None,
            }
            job.status = AutoFixJob.Status.MANUAL
            job.response_data = result
            job.save(update_fields=["status", "response_data"])
            results.append({"recommendation_id": rec.id, "status": "manual", "message": walkthrough, "fix_type": fix_type, "generated_content": generated if not gen_err else None})
            continue

        # Manual fixes — skip with detailed guidance
        if fix_type == "manual":
            manual_msg = _get_manual_walkthrough(rec, integration.provider)
            result = {
                "status": "manual",
                "message": manual_msg,
            }
            job.status = AutoFixJob.Status.MANUAL
            job.response_data = result
            job.save(update_fields=["status", "response_data"])
            results.append({"recommendation_id": rec.id, "status": "manual", "message": result["message"], "fix_type": fix_type})
            continue

        try:
            # Step 1: Generate fix content via LLM
            content, err = _generate_fix_content(fix_type, run, rec)
            if err:
                job.status = "failed"
                job.error_message = err
                job.save(update_fields=["status", "error_message"])
                results.append({"recommendation_id": rec.id, "status": "failed", "message": err, "fix_type": fix_type})
                continue

            # Step 2: Send to plugin/app to apply
            raw_result = _send_to_plugin(integration, run, fix_type, content)
            result = dict(raw_result)
            norm = _normalize_plugin_status(result.get("status"))
            result["status"] = norm
            if (
                fix_type == "llms"
                and integration.provider == "shopify"
                and norm == "success"
            ):
                result["message"] = _append_shopify_llms_hint(result.get("message"), run.url or "")

            job.status = norm
            job.response_data = result
            job.save(update_fields=["status", "response_data"])

            results.append({
                "recommendation_id": rec.id,
                "status": norm,
                "message": result.get("message", ""),
                "fix_type": fix_type,
            })
        except Exception as e:
            error_str = str(e)
            logger.exception(f"Auto-fix failed for rec {rec.id}")

            if "429" in error_str or "RATE_LIMITED" in error_str:
                msg = "Rate limited. Please try again later."
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


# ── Preview + Approve (user reviews before applying) ─────────────────────

def generate_fix_preview(run, integration, recommendation) -> dict:
    """Generate fix content via LLM and return preview WITHOUT applying."""
    fix_type = _detect_fix_type(recommendation)

    if fix_type == "manual":
        return {
            "status": "manual",
            "fix_type": "manual",
            "recommendation_id": recommendation.id,
            "recommendation_title": recommendation.title,
            "message": "This requires manual action. Follow the step-by-step instructions provided.",
        }

    # Generate fix content
    content, err = _generate_fix_content(fix_type, run, recommendation)
    if err:
        return {"status": "error", "message": err}

    # Simple types — show what will be applied
    if fix_type in ("ai_meta", "canonical", "viewport", "noindex"):
        messages = {
            "ai_meta": "AI crawler meta tags will be added (GPTBot, ClaudeBot, PerplexityBot, etc.)",
            "canonical": f"Set canonical URL to {run.url}",
            "viewport": "Set responsive viewport meta tag",
            "noindex": "Remove noindex directive and force indexing",
        }
        return {
            "status": "preview",
            "fix_type": fix_type,
            "recommendation_id": recommendation.id,
            "recommendation_title": recommendation.title,
            "original": "",
            "preview": messages.get(fix_type, content),
            "full_content": content,
            "target_type": fix_type,
        }

    # LLM-generated types
    target_type = "schema" if fix_type == "schema" else "file" if fix_type in ("llms", "robots") else "meta" if fix_type == "meta" else "content"

    return {
        "status": "preview",
        "fix_type": fix_type,
        "recommendation_id": recommendation.id,
        "recommendation_title": recommendation.title,
        "original": "",
        "preview": content[:5000],
        "full_content": content,
        "target_type": target_type,
    }


def apply_approved_fix(run, integration, recommendation, content: str, fix_type: str) -> dict:
    """Apply a user-approved fix — routes through plugin/app only."""
    result = dict(_send_to_plugin(integration, run, fix_type, content))
    norm = _normalize_plugin_status(result.get("status"))
    result["status"] = norm
    if (
        fix_type == "llms"
        and integration.provider == "shopify"
        and norm == "success"
    ):
        result["message"] = _append_shopify_llms_hint(result.get("message"), run.url or "")
    return result
