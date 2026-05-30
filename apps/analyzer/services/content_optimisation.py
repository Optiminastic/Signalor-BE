"""
Content Optimisation service.

Powers the in-app, Cursor-style content editor:
- list pages on the run's domain (sitemap audit > crawled internal links > root URL)
- fetch one page's editable fields (title, meta, body HTML, schema) preferring
  the connected plugin's /get-content for source-of-truth body, and parsing
  the public HTML for meta and embedded JSON-LD
- generate AI suggestions per page (stored as ContentSuggestion rows)
- save edits via the existing _send_to_plugin pipeline (fix_type: meta/content/schema)

Reuses, never duplicates:
- apps/analyzer/auto_fix.py: _send_to_plugin, _call_llm, _normalize_plugin_status
- apps/analyzer/integration_resolve.py: resolve_store_integration_for_run
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
from urllib.parse import urlparse

import requests
from django.utils import timezone

from apps.analyzer.auto_fix import (
    _call_llm,
    _normalize_plugin_status,
    _send_to_plugin,
)
from apps.analyzer.integration_resolve import resolve_store_integration_for_run
from apps.analyzer.models import (
    AnalysisRun,
    ContentSuggestion,
    SitemapAudit,
    SitemapAuditPage,
)

logger = logging.getLogger("apps")

# Editor field constants — must match ContentSuggestion.TARGET_FIELD_CHOICES
FIELD_TITLE = "title"
FIELD_META = "meta_description"
FIELD_BODY = "body_html"
FIELD_SCHEMA = "schema_jsonld"
ALL_FIELDS = (FIELD_TITLE, FIELD_META, FIELD_BODY, FIELD_SCHEMA)

# Plugin fix_type per editor field
_FIELD_TO_FIX_TYPE = {
    FIELD_TITLE: "meta",  # meta fix takes seo_title
    FIELD_META: "meta",  # same — packed together
    FIELD_BODY: "content",
    FIELD_SCHEMA: "schema",
}


class ContentOptimisationError(Exception):
    """Raised on unrecoverable failures (no plugin + no public HTML, etc.)."""


# ── Page list ────────────────────────────────────────────────────────────


def list_pages_for_run(run: AnalysisRun) -> list[dict]:
    """Return [{url, path, title, last_audited_at}] for the run.

    Priority: SitemapAudit pages > root URL only. We don't run a fresh crawl
    here — that's an explicit user action via the Sitemap audit panel.
    """
    audit = SitemapAudit.objects.filter(analysis_run=run).order_by("-id").first()
    if audit:
        rows = (
            SitemapAuditPage.objects.filter(audit=audit)
            .exclude(state=SitemapAuditPage.State.FAILED)
            .order_by("path", "url")
            .values("url", "path", "title")
        )
        out = [
            {
                "url": r["url"],
                "path": r["path"] or _path_of(r["url"]),
                "title": r["title"] or "",
                "last_audited_at": audit.created_at.isoformat() if audit.created_at else None,
            }
            for r in rows
        ]
        if out:
            return out

    if run.url:
        return [
            {
                "url": run.url,
                "path": _path_of(run.url) or "/",
                "title": run.brand_name or "",
                "last_audited_at": None,
            }
        ]
    return []


# ── Field fetch ──────────────────────────────────────────────────────────


def fetch_page_fields(run: AnalysisRun, url: str) -> dict:
    """Return editable fields for one page.

    Strategy:
      1. Always parse the public HTML for title, meta, embedded JSON-LD,
         and the rendered preview HTML.
      2. If a plugin integration is connected, additionally hit /get-content
         to override `body_html` with the raw post_content / page body
         (which is what the plugin will actually update on save).
    """
    if not url:
        raise ContentOptimisationError("url is required")

    integration = resolve_store_integration_for_run(run.organization, url) if run.organization_id else None

    public_html, public_text = _fetch_public_html(url)
    fields = _extract_fields_from_html(public_html, url)

    plugin_body, plugin_title, plugin_post_id = "", "", None
    if integration:
        plugin_body, plugin_title, plugin_post_id = _fetch_via_plugin(integration, url)
        if plugin_body:
            fields["body_html"] = plugin_body
        if plugin_title and not fields.get("title"):
            fields["title"] = plugin_title

    fields["url"] = url
    # Resolve the storefront password using the same fallback chain as the
    # analyzer's crawler (apps/analyzer/tasks.py): prefer the connected
    # Shopify Integration's metadata (where the onboarding form stores it),
    # fall back to the AnalysisRun field. Without this, password-protected
    # / unlaunched Shopify stores screenshot the gate instead of the store.
    storefront_password = ""
    if run.organization_id:
        try:
            from apps.integrations.models import Integration

            shop_integration = Integration.objects.filter(
                organization_id=run.organization_id,
                is_active=True,
                provider__in=["shopify", "wordpress"],
            ).first()
            if shop_integration and isinstance(shop_integration.metadata, dict):
                storefront_password = shop_integration.metadata.get("storefront_password", "") or ""
        except Exception:
            pass
    if not storefront_password:
        storefront_password = getattr(run, "storefront_password", "") or ""

    render = _capture_page_render(url, storefront_password=storefront_password)
    fields["preview_image"] = render["image"]
    fields["preview_elements"] = render["elements"]
    fields["preview_viewport_width"] = render["viewport_width"]
    fields["source"] = "plugin" if plugin_body else ("public" if public_html else "empty")
    fields["plugin_connected"] = bool(integration)
    fields["plugin_provider"] = integration.provider if integration else ""
    return fields


def _fetch_public_html(url: str) -> tuple[str, str]:
    """Fetch the raw HTML at url. Returns (html, text). Empty strings on error."""
    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SignalorBot/1.0)"},
        )
        if resp.ok:
            return resp.text, resp.text
    except Exception as exc:
        logger.debug("content_optimisation public fetch failed for %s: %s", url, exc)
    return "", ""


def _extract_fields_from_html(html: str, url: str) -> dict[str, str]:
    """Extract title, meta_description, body_html, schema_jsonld from raw HTML.

    Body extraction is a best-effort grab of <body>...</body>; for plugin-connected
    sites we override this with the source-of-truth post_content.
    """
    if not html:
        return {
            "title": "",
            "meta_description": "",
            "body_html": "",
            "schema_jsonld": "",
        }

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    title = (title_match.group(1).strip() if title_match else "")[:255]

    meta_desc = ""
    meta_match = re.search(
        r'<meta[^>]+name=[\'"]description[\'"][^>]+content=[\'"]([^\'"]*)[\'"]',
        html,
        re.IGNORECASE,
    )
    if meta_match:
        meta_desc = meta_match.group(1).strip()
    else:
        meta_match = re.search(
            r'<meta[^>]+content=[\'"]([^\'"]*)[\'"][^>]+name=[\'"]description[\'"]',
            html,
            re.IGNORECASE,
        )
        if meta_match:
            meta_desc = meta_match.group(1).strip()

    body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
    body_html = body_match.group(1)[:20000] if body_match else html[:20000]

    schema_jsonld = _extract_first_jsonld(html)

    return {
        "title": title,
        "meta_description": meta_desc,
        "body_html": body_html,
        "schema_jsonld": schema_jsonld,
    }


def _extract_first_jsonld(html: str) -> str:
    """Return the first <script type='application/ld+json'> body, pretty-printed."""
    match = re.search(
        r'<script[^>]+type=[\'"]application/ld\+json[\'"][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return ""
    raw = match.group(1).strip()
    try:
        return json.dumps(json.loads(raw), indent=2)
    except (ValueError, TypeError):
        return raw[:8000]


# Headless Chromium render via Playwright. We tried client-side iframe rendering
# of rewritten HTML, but modern themes (Shopify Dawn, lazy hydration, CSS-bg
# heroes set via JS) don't lay out without their own scripts running. A real
# browser screenshot is the only reliable preview.
_SCREENSHOT_VIEWPORT = {"width": 1440, "height": 900}
_SCREENSHOT_TIMEOUT_MS = 25000

# Tags worth letting the user click on. We deliberately exclude container
# tags (div, section, main) — too many overlapping bboxes makes the picker
# unusable. Text-bearing leaf elements only.
_PICKABLE_SELECTOR = "h1,h2,h3,h4,h5,h6,p,li,blockquote,figcaption,button,a,span[role='button']"

# Run inside the page after navigation. Returns a list of element bboxes.
# Coordinates are in CSS pixels relative to (0,0) at the document top, which
# matches the full-page screenshot's pixel grid (devicePixelRatio is 1 by
# default in Playwright).
_COLLECT_ELEMENTS_JS = """
(selector) => {
    const out = [];
    const seen = new Set();
    document.querySelectorAll(selector).forEach((el) => {
        const text = (el.innerText || el.textContent || '').trim();
        if (!text || text.length < 2) return;
        // Skip elements whose text is just their child elements' text duplicated.
        const dedupe = `${el.tagName}::${text.slice(0, 80)}`;
        if (seen.has(dedupe)) return;
        seen.add(dedupe);
        const rect = el.getBoundingClientRect();
        if (rect.width < 20 || rect.height < 8) return;
        if (rect.bottom < 0 || rect.right < 0) return;
        out.push({
            tag: el.tagName.toLowerCase(),
            text: text.length > 600 ? text.slice(0, 600) : text,
            bbox: {
                x: Math.round(rect.left + window.scrollX),
                y: Math.round(rect.top + window.scrollY),
                w: Math.round(rect.width),
                h: Math.round(rect.height),
            },
        });
    });
    return out;
}
"""


def _capture_page_render(url: str, storefront_password: str = "") -> dict:
    """Render the page in headless Chromium. Returns:
        {
            image: data-URL JPEG (full-page screenshot),
            elements: [{id, tag, text, bbox: {x,y,w,h}}],  # text-bearing leaves
            viewport_width: int,                           # CSS px width of the image
        }

    If ``storefront_password`` is provided and the page is an unlaunched
    Shopify storefront, the password gate is submitted before screenshotting
    so the caller sees the real store, not the gate.

    Returns empty fields on any failure — caller falls back gracefully.
    """
    empty = {"image": "", "elements": [], "viewport_width": _SCREENSHOT_VIEWPORT["width"]}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed; preview disabled")
        return empty

    try:
        with sync_playwright() as pw:
            # Playwright 1.49+ uses chrome-headless-shell (a separate binary)
            # by default when launching headless. On Render we've seen the
            # build's install of chrome-headless-shell silently skip on a
            # stale cache. Try the default first; if the shell is missing,
            # fall back to the full chromium binary which `playwright install
            # chromium` always lays down. Either path works for screenshots.
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as launch_exc:
                msg = str(launch_exc)
                if "chrome-headless-shell" in msg or "chromium_headless_shell" in msg:
                    logger.warning("chrome-headless-shell missing; falling back to chromium binary")
                    browser = pw.chromium.launch(
                        headless=True,
                        executable_path=pw.chromium.executable_path,
                    )
                else:
                    raise
            try:
                context = browser.new_context(
                    viewport=_SCREENSHOT_VIEWPORT,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=_SCREENSHOT_TIMEOUT_MS)

                # Shopify unlaunched-store gate: if the URL contains
                # `/password` or there's a visible password form, submit
                # the stored password and wait for redirect to the real
                # storefront. Doing this here (instead of inside the
                # crawler) so screenshots match what real visitors see
                # once they pass the gate.
                if storefront_password:
                    try:
                        if page.locator('form[action*="password"]').count() > 0 or "/password" in page.url:
                            password_input = page.locator('input[name="password"]').first
                            if password_input.is_visible(timeout=1500):
                                password_input.fill(storefront_password)
                                # Submit via Enter (works regardless of button selector drift).
                                password_input.press("Enter")
                                page.wait_for_load_state("domcontentloaded", timeout=8000)
                    except Exception as gate_exc:
                        logger.info("storefront password submit skipped: %s", gate_exc)
                # Wait for the network to quiet down so JS-rendered cards and
                # lazy-loaded hero images have a chance to appear. Fall back to
                # a short fixed wait if networkidle never fires (some sites with
                # always-on pixels / heartbeats won't go idle).
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    page.wait_for_timeout(2500)

                # Trigger lazy-load by scrolling the page top-to-bottom, then
                # back to the top so the screenshot starts above the fold.
                try:
                    page.evaluate(
                        """async () => {
                            const step = window.innerHeight;
                            for (let y = 0; y < document.body.scrollHeight; y += step) {
                                window.scrollTo(0, y);
                                await new Promise(r => setTimeout(r, 120));
                            }
                            window.scrollTo(0, 0);
                            await new Promise(r => setTimeout(r, 250));
                        }"""
                    )
                except Exception:
                    pass

                # Capture elements first so DOM hasn't been disturbed by scroll.
                raw_elements = page.evaluate(_COLLECT_ELEMENTS_JS, _PICKABLE_SELECTOR)
                png = page.screenshot(full_page=True, type="jpeg", quality=80)
            finally:
                browser.close()

        elements = [{"id": idx, **el} for idx, el in enumerate(raw_elements or [])]
        return {
            "image": f"data:image/jpeg;base64,{base64.b64encode(png).decode('ascii')}",
            "elements": elements,
            "viewport_width": _SCREENSHOT_VIEWPORT["width"],
        }
    except Exception as exc:
        logger.warning("playwright capture failed for %s: %s", url, exc)
        return empty


def _fetch_via_plugin(integration, url: str) -> tuple[str, str, int | None]:
    """Call the plugin's /get-content. Returns (body, title, post_id)."""
    provider = integration.provider
    try:
        if provider == "wordpress":
            site_url = integration.metadata.get("site_url", "")
            api_key = integration.metadata.get("signalor_api_key", "")
            if not site_url or not api_key:
                return "", "", None
            resp = requests.post(
                f"{site_url.rstrip('/')}/wp-json/signalor/v1/get-content",
                headers={"X-Signalor-Key": api_key, "Content-Type": "application/json"},
                json={"url": url},
                timeout=15,
            )
            if not resp.ok:
                return "", "", None
            data = resp.json()
            return data.get("content", "") or "", data.get("title", "") or "", data.get("post_id")

        if provider == "shopify":
            app_url = integration.metadata.get("signalor_app_url", "").rstrip("/")
            hmac_secret = integration.metadata.get("signalor_hmac_secret", "")
            shop = integration.metadata.get("shop_domain", "")
            if not app_url or not hmac_secret:
                return "", "", None
            payload = {"url": url, "shop": shop}
            body = json.dumps(payload).encode("utf-8")
            signature = hmac.new(hmac_secret.encode(), body, hashlib.sha256).hexdigest()
            resp = requests.post(
                f"{app_url}/api/get-content",
                headers={
                    "X-Signalor-Signature": signature,
                    "X-Signalor-Shop": shop,
                    "Content-Type": "application/json",
                },
                data=body,
                timeout=20,
            )
            if not resp.ok:
                return "", "", None
            data = resp.json()
            return data.get("content", "") or "", data.get("title", "") or "", None
    except Exception as exc:
        logger.debug("content_optimisation plugin fetch failed: %s", exc)
    return "", "", None


# ── AI Suggestions ───────────────────────────────────────────────────────

_SUGGEST_PROMPT = """You are an AI search optimisation expert. Given a webpage, suggest 4-7 concrete edits that will make it more discoverable and quotable by AI search engines (ChatGPT, Perplexity, Gemini, Claude).

Each suggestion must target ONE editor field:
- "title" — page <title>
- "meta_description" — meta description
- "body_html" — main content HTML
- "schema_jsonld" — JSON-LD structured data block

For body_html and schema_jsonld, prefer additive suggestions (add a FAQ section, add summary paragraph, add Article schema, add HowTo schema) rather than full rewrites.

Return STRICT JSON — an array of objects, no surrounding text:
[
  {{
    "title": "Short label, e.g. 'Add FAQ schema'",
    "rationale": "One sentence explaining why this helps AI search.",
    "target_field": "title|meta_description|body_html|schema_jsonld",
    "current_excerpt": "What's there now (truncated, can be empty if adding new)",
    "proposed_value": "The exact value to write — full title, full meta, full body HTML, or full JSON-LD"
  }}
]

PAGE URL: {url}
CURRENT TITLE: {title}
CURRENT META: {meta}
CURRENT SCHEMA (first 1000 chars): {schema}
CURRENT BODY (first 4000 chars): {body}
"""


def generate_suggestions(run: AnalysisRun, url: str, fields: dict | None = None) -> list[ContentSuggestion]:
    """Generate fresh AI suggestions for a page. Old PROPOSED rows on the
    same (run, url) are dismissed so the UI shows only the latest set."""
    if fields is None:
        fields = fetch_page_fields(run, url)

    prompt = _SUGGEST_PROMPT.format(
        url=url,
        title=(fields.get("title") or "")[:200],
        meta=(fields.get("meta_description") or "")[:300],
        schema=(fields.get("schema_jsonld") or "")[:1000],
        body=(fields.get("body_html") or "")[:4000],
    )

    raw = _call_llm(prompt, purpose="content-optimisation-suggest")
    parsed = _parse_suggestions_json(raw)
    if not parsed:
        return []

    # Dismiss prior proposed-but-unused rows on this page
    ContentSuggestion.objects.filter(
        analysis_run=run,
        url=url,
        status=ContentSuggestion.PROPOSED,
    ).update(status=ContentSuggestion.DISMISSED)

    created: list[ContentSuggestion] = []
    valid_targets = set(ALL_FIELDS)
    for item in parsed[:10]:
        target = (item.get("target_field") or "").strip()
        if target not in valid_targets:
            continue
        proposed = (item.get("proposed_value") or "").strip()
        if not proposed:
            continue
        suggestion = ContentSuggestion.objects.create(
            analysis_run=run,
            url=url,
            title=(item.get("title") or "")[:255] or "Suggested edit",
            rationale=(item.get("rationale") or "")[:1000],
            target_field=target,
            current_excerpt=(item.get("current_excerpt") or "")[:2000],
            proposed_value=proposed[:20000],
        )
        created.append(suggestion)
    return created


def _parse_suggestions_json(raw: str) -> list[dict]:
    """LLMs often wrap JSON in markdown fences. Strip and parse leniently."""
    if not raw:
        return []
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    # Find first [ ... last ] to be robust to leading/trailing prose
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(cleaned[start : end + 1])
        return [d for d in data if isinstance(d, dict)]
    except (ValueError, TypeError):
        logger.warning("content_optimisation: failed to parse LLM JSON")
        return []


def list_active_suggestions(run: AnalysisRun, url: str) -> list[ContentSuggestion]:
    return list(
        ContentSuggestion.objects.filter(
            analysis_run=run, url=url, status=ContentSuggestion.PROPOSED
        ).order_by("-created_at")
    )


def dismiss_suggestion(run: AnalysisRun, suggestion_id: int) -> ContentSuggestion | None:
    try:
        s = ContentSuggestion.objects.get(id=suggestion_id, analysis_run=run)
    except ContentSuggestion.DoesNotExist:
        return None
    if s.status == ContentSuggestion.PROPOSED:
        s.status = ContentSuggestion.DISMISSED
        s.save(update_fields=["status"])
    return s


# ── Save (push to plugin) ────────────────────────────────────────────────


def save_page_edits(run: AnalysisRun, url: str, edits: dict[str, str]) -> dict:
    """Push the edited fields to the connected plugin.

    `edits` is a partial map of editor field -> new value. Only non-None values
    are pushed. Returns:
      {
        saved: [field, ...],
        failed: [{field, message}, ...],
        plugin_responses: {field: response_dict},
      }
    """
    integration = resolve_store_integration_for_run(run.organization, url) if run.organization_id else None
    if not integration:
        raise ContentOptimisationError(
            "No active WordPress or Shopify integration. Connect one in Settings → Integrations."
        )

    saved: list[str] = []
    failed: list[dict] = []
    responses: dict[str, dict] = {}

    # Title + meta share the plugin's `meta` fix_type — pack them together.
    title = edits.get(FIELD_TITLE)
    meta = edits.get(FIELD_META)
    if title is not None or meta is not None:
        meta_payload = json.dumps(
            {
                "seo_title": title or "",
                "seo_description": meta or "",
            }
        )
        result = _push_one(run, integration, url, "meta", meta_payload)
        applied_fields = [f for f in (FIELD_TITLE, FIELD_META) if edits.get(f) is not None]
        if result.get("normalized_status") == "success":
            saved.extend(applied_fields)
        else:
            for f in applied_fields:
                failed.append({"field": f, "message": result.get("message", "Plugin error")})
        for f in applied_fields:
            responses[f] = result

    body = edits.get(FIELD_BODY)
    if body is not None:
        result = _push_one(run, integration, url, "content", body)
        if result.get("normalized_status") == "success":
            saved.append(FIELD_BODY)
        else:
            failed.append({"field": FIELD_BODY, "message": result.get("message", "Plugin error")})
        responses[FIELD_BODY] = result

    schema = edits.get(FIELD_SCHEMA)
    if schema is not None:
        result = _push_one(run, integration, url, "schema", schema)
        if result.get("normalized_status") == "success":
            saved.append(FIELD_SCHEMA)
        else:
            failed.append({"field": FIELD_SCHEMA, "message": result.get("message", "Plugin error")})
        responses[FIELD_SCHEMA] = result

    return {"saved": saved, "failed": failed, "plugin_responses": responses}


def _push_one(run: AnalysisRun, integration, url: str, fix_type: str, content: str) -> dict:
    """Call _send_to_plugin. Returns the response with `normalized_status` added.

    `_send_to_plugin` reads run.url as the target page. We temporarily override
    it so the plugin targets the page being edited, not the run's home URL.
    """
    original_url = run.url
    try:
        run.url = url
        result = _send_to_plugin(integration, run, fix_type, content) or {}
    finally:
        run.url = original_url

    result["normalized_status"] = _normalize_plugin_status(result.get("status"))
    return result


def mark_suggestion_used(run: AnalysisRun, suggestion_id: int) -> ContentSuggestion | None:
    try:
        s = ContentSuggestion.objects.get(id=suggestion_id, analysis_run=run)
    except ContentSuggestion.DoesNotExist:
        return None
    s.status = ContentSuggestion.USED
    s.used_at = timezone.now()
    s.save(update_fields=["status", "used_at"])
    return s


# ── Element-level edit (Cursor-style click → rewrite → apply) ────────────

_REWRITE_ELEMENT_PROMPT = """You are a content editor optimizing a page for AI search engines (ChatGPT, Perplexity, Gemini, Claude).

Rewrite the following text element. Keep roughly the same length unless the instruction says otherwise. Make it clearer, more specific, and easier for AI engines to quote. Keep the same tone and language.

ELEMENT TYPE: {tag}
ORIGINAL TEXT:
{text}

{instruction_block}
Return ONLY the rewritten text. No quotes, no markdown, no explanation."""


def rewrite_element_text(tag: str, text: str, instruction: str = "") -> str:
    """Ask the LLM to rewrite one element's text. Returns the new text, or
    the original if the call fails."""
    text = (text or "").strip()
    if not text:
        return ""
    instruction_block = f"INSTRUCTION FROM USER: {instruction.strip()}\n" if instruction.strip() else ""
    prompt = _REWRITE_ELEMENT_PROMPT.format(
        tag=tag or "p",
        text=text,
        instruction_block=instruction_block,
    )
    raw = _call_llm(prompt, purpose="content-optimisation-rewrite-element") or ""
    cleaned = raw.strip()
    # Strip surrounding quote chars some models add despite the instruction.
    if len(cleaned) >= 2 and cleaned[0] in "\"'“‘" and cleaned[-1] in "\"'”’":
        cleaned = cleaned[1:-1].strip()
    return cleaned or text


def apply_element_edit(
    run: AnalysisRun,
    url: str,
    original_text: str,
    new_text: str,
) -> dict:
    """Replace `original_text` inside the page's body_html with `new_text`,
    then push the updated body_html through the connected plugin.

    First-occurrence replacement — fragile when the same text appears multiple
    times, but acceptable for v1 since most landing pages have unique copy.
    Raises ContentOptimisationError if no plugin is connected or text isn't
    found.
    """
    original_text = (original_text or "").strip()
    new_text = (new_text or "").strip()
    if not original_text or not new_text:
        raise ContentOptimisationError("original and new text are required")
    if original_text == new_text:
        return {"saved": [], "failed": [], "plugin_responses": {}, "noop": True}

    fields = fetch_page_fields(run, url)
    body_html = fields.get("body_html") or ""

    # Try the Pages-API path first — fast, no Shopify Asset round-trip.
    if body_html:
        new_body, replaced = _replace_first_text_in_html(body_html, original_text, new_text)
        if replaced:
            return save_page_edits(run, url, {FIELD_BODY: new_body})

    # Fall back to Shopify theme-asset edit. Most homepage / hero / nav /
    # footer text lives in sections/*.json, not in any Page body_html. The
    # connected Signalor Shopify Integration has write_themes scope so we
    # can update those files directly via the Asset API.
    if run.organization_id:
        try:
            from apps.integrations.models import Integration
            from apps.integrations.services.shopify_theme import (
                ThemeEditError,
                find_and_replace_text,
            )

            shop_integration = Integration.objects.filter(
                organization_id=run.organization_id,
                provider=Integration.Provider.SHOPIFY,
                is_active=True,
            ).first()
            if shop_integration:
                try:
                    result = find_and_replace_text(shop_integration, original_text, new_text, url=url)
                except ThemeEditError as exc:
                    raise ContentOptimisationError(str(exc)) from exc
                if result.get("ok"):
                    # Match the canonical save_page_edits return shape so the
                    # FE's Zod schema parses it. `body_html` is used as the
                    # placeholder field-name (closest semantic equivalent for
                    # "page body content was changed"); the real provenance
                    # of the edit goes into plugin_responses for debugging.
                    return {
                        "saved": ["body_html"],
                        "failed": [],
                        "plugin_responses": {
                            "type": "theme_asset",
                            "asset_key": result["asset_key"],
                            "preview": result.get("preview", ""),
                        },
                    }
        except ContentOptimisationError:
            raise
        except Exception:
            logger.exception("shopify_theme fallback failed")

    raise ContentOptimisationError(
        "Couldn't locate that text in the page body or in your theme. "
        "It may be rendered by JavaScript or come from translation strings. "
        "Try editing via Generate suggestions."
    )


def _replace_first_text_in_html(html: str, needle: str, replacement: str) -> tuple[str, bool]:
    """Replace the first occurrence of `needle` (text content, ignoring
    surrounding tags/whitespace) with `replacement`. Returns (new_html, ok).

    Strategy: try a direct substring replace first; if the needle has internal
    whitespace, also try a whitespace-flexible regex match before giving up.
    """
    if not needle or not html:
        return html, False
    if needle in html:
        return html.replace(needle, replacement, 1), True

    # Whitespace-flexible match: collapse runs of whitespace in the needle into
    # `\s+`. Catches cases where the rendered text has single spaces but the
    # source HTML has newlines / multiple spaces.
    flexible = re.escape(needle)
    flexible = re.sub(r"\\\s+", r"\\s+", flexible)
    match = re.search(flexible, html, flags=re.IGNORECASE)
    if not match:
        return html, False
    return html[: match.start()] + replacement + html[match.end() :], True


# ── helpers ──────────────────────────────────────────────────────────────


def _path_of(url: str) -> str:
    try:
        p = urlparse(url).path or "/"
        return p
    except Exception:
        return ""
