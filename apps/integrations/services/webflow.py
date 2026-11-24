"""
Webflow Data API v2 client + OAuth helpers.

Unlike WordPress/Shopify (where a plugin/app on the customer's site receives
fixes), Webflow has no plugin model — Signalor talks to Webflow's own Data API
directly with an OAuth access token stored (encrypted) on the Integration row.

What Signalor can write via the Data API:
  - Page SEO title/description        → PATCH /pages/{page_id}   (scope pages:write)
  - Visible text on a page (DOM nodes) → POST  /pages/{page_id}/dom
  - Publish the site so edits go live  → POST  /sites/{site_id}/publish (sites:write)

What Webflow does NOT expose over the API (these stay "manual"):
  - root llms.txt / robots.txt, arbitrary <script> injection on hosting plans
    without custom_code scope, adding brand-new page sections.

Docs: https://developers.webflow.com/data/reference
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlencode, urlparse

import requests

logger = logging.getLogger("apps")

API_BASE = "https://api.webflow.com/v2"
OAUTH_AUTHORIZE_URL = "https://webflow.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://api.webflow.com/oauth/access_token"

# Scopes we request. sites:* to list/publish, pages:* to read/write SEO + DOM,
# cms:* for blog publishing, custom_code:* to inject JSON-LD schema into <head>,
# authorized_user:read to validate the token.
OAUTH_SCOPES = [
    "sites:read",
    "sites:write",
    "pages:read",
    "pages:write",
    "cms:read",
    "cms:write",
    "custom_code:read",
    "custom_code:write",
    "authorized_user:read",
]

_TIMEOUT = 25


class WebflowError(Exception):
    """Any non-2xx from the Webflow API, or a local precondition failure."""


class WebflowNotConfigured(Exception):
    """OAuth client credentials are not set on this server."""


# ── OAuth ──────────────────────────────────────────────────────────────────


def oauth_config() -> tuple[str, str, str]:
    """Return (client_id, client_secret, redirect_uri) or raise WebflowNotConfigured."""
    client_id = os.getenv("WEBFLOW_CLIENT_ID", "").strip()
    client_secret = os.getenv("WEBFLOW_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("WEBFLOW_REDIRECT_URI", "").strip()
    if not (client_id and client_secret and redirect_uri):
        raise WebflowNotConfigured(
            "Webflow OAuth is not configured on this server "
            "(WEBFLOW_CLIENT_ID / WEBFLOW_CLIENT_SECRET / WEBFLOW_REDIRECT_URI)."
        )
    return client_id, client_secret, redirect_uri


def build_authorize_url(state: str) -> str:
    """Build the Webflow consent-screen URL to redirect the user to."""
    client_id, _, redirect_uri = oauth_config()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(OAUTH_SCOPES),
        "state": state,
    }
    return f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str) -> str:
    """Exchange an authorization code for an access token. Returns the token."""
    client_id, client_secret, redirect_uri = oauth_config()
    resp = requests.post(
        OAUTH_TOKEN_URL,
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        headers={"accept": "application/json"},
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        logger.warning("Webflow token exchange failed (%d): %s", resp.status_code, resp.text[:300])
        raise WebflowError(f"Token exchange failed ({resp.status_code}).")
    token = (resp.json() or {}).get("access_token", "")
    if not token:
        raise WebflowError("Token exchange returned no access_token.")
    return token


# ── API client ─────────────────────────────────────────────────────────────


class WebflowClient:
    """Thin wrapper over the Webflow Data API v2 using a bearer access token."""

    def __init__(self, access_token: str):
        if not access_token:
            raise WebflowError("Missing Webflow access token.")
        self._token = access_token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "accept": "application/json",
            "content-type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{API_BASE}{path}"
        try:
            resp = requests.request(
                method, url, headers=self._headers(), timeout=_TIMEOUT, **kwargs
            )
        except requests.RequestException as exc:
            raise WebflowError(f"Could not reach Webflow: {exc}") from exc
        if resp.status_code == 401:
            raise WebflowError("Webflow rejected the access token (401). Reconnect Webflow.")
        if resp.status_code == 429:
            raise WebflowError("Webflow rate limit hit (429). Try again shortly.")
        if not resp.ok:
            logger.warning("Webflow %s %s -> %d: %s", method, path, resp.status_code, resp.text[:300])
            detail = ""
            try:
                detail = (resp.json() or {}).get("message", "")
            except ValueError:
                pass
            raise WebflowError(
                f"Webflow API error ({resp.status_code}): {detail}"
                if detail
                else f"Webflow API error ({resp.status_code})."
            )
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    # ── identity / sites ────────────────────────────────────────────────

    def get_authorized_user(self) -> dict:
        return self._request("GET", "/token/authorized_by")

    def list_sites(self) -> list[dict]:
        data = self._request("GET", "/sites")
        return data.get("sites", []) if isinstance(data, dict) else []

    def get_site(self, site_id: str) -> dict:
        return self._request("GET", f"/sites/{site_id}")

    def publish_site(self, site_id: str, custom_domain_ids: list[str] | None = None) -> dict:
        """Publish the site so edits go live. Publishes to the Webflow subdomain
        and any custom domains passed."""
        body: dict = {"publishToWebflowSubdomain": True}
        if custom_domain_ids:
            body["customDomains"] = custom_domain_ids
        return self._request("POST", f"/sites/{site_id}/publish", json=body)

    def publish_site_all_domains(self, site_id: str) -> dict:
        """Publish to the Webflow subdomain AND every custom domain on the site."""
        domain_ids: list[str] = []
        try:
            site = self.get_site(site_id)
            for d in site.get("customDomains", []) or []:
                did = d.get("id")
                if did:
                    domain_ids.append(did)
        except WebflowError:
            pass
        return self.publish_site(site_id, domain_ids or None)

    # ── pages ───────────────────────────────────────────────────────────

    def list_pages(self, site_id: str) -> list[dict]:
        """List all pages on a site (paginated internally)."""
        pages: list[dict] = []
        offset = 0
        limit = 100
        while True:
            data = self._request(
                "GET", f"/sites/{site_id}/pages", params={"limit": limit, "offset": offset}
            )
            batch = data.get("pages", []) if isinstance(data, dict) else []
            pages.extend(batch)
            total = (data.get("pagination") or {}).get("total", len(pages))
            offset += limit
            if offset >= total or not batch:
                break
        return pages

    def get_page(self, page_id: str) -> dict:
        return self._request("GET", f"/pages/{page_id}")

    def update_page_seo(self, page_id: str, title: str | None, description: str | None) -> dict:
        """Update a page's SEO title/description. Only sends fields provided.

        Webflow's "Update Page Settings" is a PUT (PATCH returns RouteNotFound).
        It updates just the fields sent and preserves the rest.
        """
        seo: dict = {}
        if title is not None:
            seo["title"] = title
        if description is not None:
            seo["description"] = description
        if not seo:
            raise WebflowError("Nothing to update (no title/description).")
        return self._request("PUT", f"/pages/{page_id}", json={"seo": seo})

    def get_site_locales(self, site_id: str) -> tuple[str | None, list[str]]:
        """Return (primary_locale_id, [secondary_locale_ids]) for a site.

        Both are None/empty when the site has no localization configured.
        Webflow's DOM-write API only accepts *secondary* locale ids.
        """
        site = self.get_site(site_id)
        locales = site.get("locales") or {}
        primary = locales.get("primary") or {}
        primary_id = primary.get("cmsLocaleId") or primary.get("id")
        secondary = [
            (loc.get("cmsLocaleId") or loc.get("id"))
            for loc in (locales.get("secondary") or [])
            if (loc.get("cmsLocaleId") or loc.get("id"))
        ]
        return primary_id, secondary

    def get_page_dom(self, page_id: str, locale_id: str | None = None) -> list[dict]:
        """Return all DOM nodes for a page (paginated internally)."""
        nodes: list[dict] = []
        offset = 0
        limit = 100
        while True:
            params: dict = {"limit": limit, "offset": offset}
            if locale_id:
                params["localeId"] = locale_id
            data = self._request("GET", f"/pages/{page_id}/dom", params=params)
            batch = data.get("nodes", []) if isinstance(data, dict) else []
            nodes.extend(batch)
            total = (data.get("pagination") or {}).get("total", len(nodes))
            offset += limit
            if offset >= total or not batch:
                break
        return nodes

    def update_page_dom_nodes(
        self, page_id: str, nodes: list[dict], locale_id: str | None = None
    ) -> dict:
        """Update text nodes. `nodes` is [{"nodeId": ..., "text": "<html>"}]."""
        if not nodes:
            raise WebflowError("No nodes to update.")
        params = {"localeId": locale_id} if locale_id else None
        return self._request(
            "POST", f"/pages/{page_id}/dom", params=params, json={"nodes": nodes}
        )

    # ── custom code (site-level JSON-LD / script injection) ─────────────

    def register_inline_script(
        self, site_id: str, source_code: str, display_name: str, version: str
    ) -> dict:
        """Register an inline custom-code script for a site. Returns {id, ...}.

        Webflow keys registered scripts by (displayName, version) and treats
        them as immutable — re-registering the same pair 409s, so callers should
        vary `version` when the content changes and fall back to
        list_registered_scripts to find an existing one.
        """
        return self._request(
            "POST",
            f"/sites/{site_id}/registered_scripts/inline",
            json={
                "sourceCode": source_code,
                "displayName": display_name,
                "version": version,
                "canCopy": False,
            },
        )

    def list_registered_scripts(self, site_id: str) -> list[dict]:
        data = self._request("GET", f"/sites/{site_id}/registered_scripts")
        return data.get("registeredScripts", []) if isinstance(data, dict) else []

    def get_site_custom_code(self, site_id: str) -> list[dict]:
        """Return the scripts currently applied to the site's custom code."""
        try:
            data = self._request("GET", f"/sites/{site_id}/custom_code")
        except WebflowError:
            return []
        return data.get("scripts", []) if isinstance(data, dict) else []

    def apply_site_custom_code(self, site_id: str, scripts: list[dict]) -> dict:
        """Upsert the full list of applied scripts for a site (replaces list)."""
        return self._request(
            "PUT", f"/sites/{site_id}/custom_code", json={"scripts": scripts}
        )


# ── URL → page resolution ────────────────────────────────────────────────


def _path_of(url: str) -> str:
    try:
        return (urlparse(url).path or "/").rstrip("/") or "/"
    except Exception:
        return "/"


def resolve_page_for_url(pages: list[dict], url: str) -> dict | None:
    """Best-effort: match an analyzed URL to a Webflow page by slug/path.

    Webflow pages carry a `slug` (empty for the homepage) and an `isHomePage`
    flag. We match the URL's path against `/{slug}`; the homepage matches the
    root path.
    """
    target = _path_of(url)

    # Root path → the homepage. Webflow returns slug=None (not "") and often
    # omits isHomePage, so normalize before comparing.
    if target == "/":
        for p in pages:
            slug = (p.get("slug") or "").strip("/").lower()
            if p.get("isHomePage") or slug in ("", "index", "home"):
                return p

    for p in pages:
        slug = (p.get("slug") or "").strip("/")
        if slug and f"/{slug}" == target:
            return p

    # Fallback: last path segment matches slug (nested folders).
    last = target.rstrip("/").split("/")[-1]
    for p in pages:
        if (p.get("slug") or "").strip("/") == last and last:
            return p

    return None


# ── High-level operations used by auto_fix / content_optimisation ─────────


def _client_from_integration(integration) -> WebflowClient:
    token = integration.get_access_token()
    if not token:
        raise WebflowError("Webflow is connected but has no access token. Reconnect Webflow.")
    return WebflowClient(token)


def _site_id_from_integration(integration) -> str:
    site_id = (integration.metadata or {}).get("site_id", "")
    if not site_id:
        raise WebflowError("No Webflow site selected for this integration.")
    return site_id


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or url).lower().split(":")[0]
    except Exception:
        return (url or "").lower()


def _site_domains(site: dict) -> list[str]:
    """Real hostnames a Webflow site is reachable at (webflow.io + custom).

    Note: ``previewUrl`` is deliberately excluded — Webflow returns a shared
    ``screenshots.webflow.com`` thumbnail URL there, not the site's address, so
    matching on it would collide across every site on the account.
    """
    out: list[str] = []
    short = site.get("shortName")
    if short:
        out.append(f"{short}.webflow.io")
    for d in site.get("customDomains", []) or []:
        if d.get("url"):
            out.append(_host_of(d["url"]))
    return [d for d in out if d]


def resolve_site_id_for_url(integration, url: str = "") -> str:
    """Return the Webflow site_id for this integration.

    Prefers the explicitly selected site. If none is selected but `url`'s host
    matches one of the account's sites, auto-select and persist that site so the
    editor/auto-fix work without a manual pick. Raises WebflowError if nothing
    can be resolved.
    """
    meta = integration.metadata or {}
    if meta.get("site_id"):
        return meta["site_id"]

    host = _host_of(url)
    client = _client_from_integration(integration)
    sites = client.list_sites()

    match = None
    if host:
        for s in sites:
            if any(host == d or host.endswith("." + d) or d.endswith("." + host) for d in _site_domains(s)):
                match = s
                break
    # Single authorized site → unambiguous, select it.
    if match is None and len(sites) == 1:
        match = sites[0]

    if match is None:
        raise WebflowError(
            "No Webflow site selected for this integration. "
            "Pick a site in Settings → Integrations."
        )

    meta["site_id"] = match["id"]
    meta["site_name"] = match.get("displayName") or match.get("shortName") or match["id"]
    integration.metadata = meta
    try:
        integration.save(update_fields=["metadata", "updated_at"])
    except Exception:
        logger.debug("Could not persist auto-selected Webflow site_id", exc_info=True)
    return match["id"]


def _safe_publish(client: "WebflowClient", site_id: str) -> bool:
    """Publish, tolerating Webflow's 1-publish-per-minute rate limit.

    Returns True if published, False if deferred (e.g. 429) — the edit is
    already saved either way and goes live on the next successful publish.
    """
    try:
        client.publish_site_all_domains(site_id)
        return True
    except WebflowError as exc:
        logger.info("Webflow publish deferred: %s", exc)
        return False


def apply_seo_to_url(integration, url: str, seo_title: str, seo_description: str) -> dict:
    """Resolve the page for `url`, update its SEO, and publish. Returns a
    plugin-style result dict ({status, message})."""
    client = _client_from_integration(integration)
    site_id = resolve_site_id_for_url(integration, url)
    pages = client.list_pages(site_id)
    page = resolve_page_for_url(pages, url)
    if not page:
        return {
            "status": "failed",
            "message": "Could not match this URL to a Webflow page. Open the page in Webflow and try again.",
        }
    client.update_page_seo(page["id"], seo_title or None, seo_description or None)
    published = _safe_publish(client, site_id)
    msg = "Updated SEO metadata in Webflow" + (
        " and published."
        if published
        else " (publish is rate-limited right now; it will go live on your next publish)."
    )
    changes = []
    if seo_title:
        changes.append(f'SEO title → "{seo_title}"')
    if seo_description:
        changes.append(f'Meta description → "{seo_description}"')
    return {
        "status": "success",
        "message": msg,
        "changed_url": url,
        "changes": "  ·  ".join(changes),
    }


def replace_visible_text_on_url(
    integration, url: str, original_text: str, new_text: str, tag: str = ""
) -> dict:
    """Find the DOM text node on `url`'s page whose plain text matches
    `original_text`, replace it with `new_text`, and publish.

    Matching is case-insensitive (rendered text is often upper/lower-cased by
    CSS ``text-transform`` while the source keeps its own case). When the same
    text appears in several nodes (e.g. a nav link and a heading both say
    "About"), the optional `tag` (the clicked element's HTML tag, e.g. "h2")
    disambiguates which node to edit.

    Returns {"ok": bool, "message": str, "node_id": str}.
    """
    client = _client_from_integration(integration)
    site_id = resolve_site_id_for_url(integration, url)

    # Webflow's DOM-write API (POST /pages/{id}/dom) ONLY accepts a *secondary*
    # locale id — the primary/default locale's page text is not editable via the
    # API. Sites without Localization have no secondary locale, so on-page text
    # simply cannot be changed through Signalor there.
    _, secondary_locales = client.get_site_locales(site_id)
    if not secondary_locales:
        return {
            "ok": False,
            "message": (
                "Webflow's API can't edit primary-locale page text — it only allows "
                "text edits on a site's secondary (localized) locale, and this site "
                "has none. Edit this text in the Webflow Designer. (SEO title/description "
                "and CMS content CAN be updated from Signalor.)"
            ),
        }
    locale_id = secondary_locales[0]

    pages = client.list_pages(site_id)
    page = resolve_page_for_url(pages, url)
    if not page:
        return {"ok": False, "message": "Could not match this URL to a Webflow page."}

    nodes = client.get_page_dom(page["id"])
    original_lc = " ".join((original_text or "").split()).lower()
    tag_lc = (tag or "").strip().lower()

    def _node_plain(node: dict) -> str:
        t = node.get("text") or {}
        return " ".join((t.get("text") or "").split())

    def _node_html(node: dict) -> str:
        return (node.get("text") or {}).get("html") or ""

    def _tag_matches(node: dict) -> bool:
        if not tag_lc:
            return False
        html = _node_html(node).lstrip().lower()
        return html.startswith(f"<{tag_lc}>") or html.startswith(f"<{tag_lc} ")

    text_nodes = [n for n in nodes if n.get("type") == "text"]
    exact = [n for n in text_nodes if original_lc and _node_plain(n).lower() == original_lc]
    candidates = exact or [
        n for n in text_nodes if original_lc and original_lc in _node_plain(n).lower()
    ]

    if not candidates:
        return {
            "ok": False,
            "message": (
                "Couldn't find that text on the Webflow page. It may be inside a "
                "symbol/component or a CMS field — edit it in the Webflow Designer."
            ),
        }

    # Prefer the candidate whose HTML tag matches the clicked element.
    match = next((n for n in candidates if _tag_matches(n)), candidates[0])

    # Preserve the node's existing HTML wrapper: swap the inner plain text for
    # the new text so we don't strip formatting. If the original HTML equals the
    # plain text, just send the new text.
    existing = match.get("text") or {}
    existing_html = existing.get("html") or new_text
    existing_plain = existing.get("text") or ""
    if existing_plain and existing_plain in existing_html:
        replacement_html = existing_html.replace(existing_plain, new_text, 1)
    else:
        replacement_html = new_text

    client.update_page_dom_nodes(
        page["id"], [{"nodeId": match["id"], "text": replacement_html}], locale_id=locale_id
    )
    published = _safe_publish(client, site_id)
    msg = "Updated text in Webflow" + (
        " and published." if published else " (publish is rate-limited; it will go live on your next publish)."
    )
    return {"ok": True, "message": msg, "node_id": match["id"]}


# ── Schema (JSON-LD) injection via site custom code ──────────────────────

_SCHEMA_SCRIPT_NAME = "signalorschema"  # displayName must be alphanumeric

_CUSTOM_CODE_SCOPE_MSG = (
    "Signalor's Webflow connection can't inject schema yet — it's missing the "
    "Custom Code permission. In your Webflow App's Data client, enable "
    "custom_code:read and custom_code:write, then reconnect Webflow in "
    "Settings → Integrations and try again."
)


def _is_scope_error(exc: Exception) -> bool:
    m = str(exc).lower()
    return "403" in m or "missing_scopes" in m or "custom_code" in m or "scope" in m


def _extract_jsonld(schema_html: str) -> str:
    """Pull the JSON out of a <script type='application/ld+json'> blob and return
    it as a compact JSON string, or '' if it isn't valid JSON."""
    import json as _json
    import re

    m = re.search(r"<script[^>]*>(.*?)</script>", schema_html or "", re.DOTALL | re.IGNORECASE)
    inner = (m.group(1) if m else (schema_html or "")).strip()
    try:
        return _json.dumps(_json.loads(inner))
    except (ValueError, TypeError):
        return ""


def apply_schema_via_custom_code(integration, url: str, schema_html: str) -> dict:
    """Inject JSON-LD into the site <head> via Webflow's custom-code API, then
    publish. Requires the connection to hold custom_code:read/write scopes.

    Returns a plugin-style {status, message} dict.
    """
    import hashlib
    import json as _json

    jsonld = _extract_jsonld(schema_html)
    if not jsonld:
        return {"status": "failed", "message": "The generated schema wasn't valid JSON-LD."}

    client = _client_from_integration(integration)
    site_id = resolve_site_id_for_url(integration, url)

    # Inline JS that injects (or replaces) our JSON-LD <script> in the head.
    js = (
        "(function(){try{var d=document,i='signalor-jsonld';"
        "var o=d.getElementById(i);if(o)o.remove();"
        "var s=d.createElement('script');s.type='application/ld+json';s.id=i;"
        "s.text=" + _json.dumps(jsonld) + ";d.head.appendChild(s);}catch(e){}})();"
    )
    # Version derived from content so updated schema registers a fresh version.
    version = f"1.0.{int(hashlib.sha1(jsonld.encode()).hexdigest()[:6], 16) % 1000}"

    # 1) Register the inline script (reuse an existing registration on conflict).
    script_id = None
    try:
        reg = client.register_inline_script(site_id, js, _SCHEMA_SCRIPT_NAME, version)
        script_id = reg.get("id")
    except WebflowError as exc:
        if _is_scope_error(exc):
            return {"status": "failed", "message": _CUSTOM_CODE_SCOPE_MSG}
        try:
            registered = client.list_registered_scripts(site_id)
        except WebflowError as exc2:
            if _is_scope_error(exc2):
                return {"status": "failed", "message": _CUSTOM_CODE_SCOPE_MSG}
            registered = []
        for rs in registered:
            if rs.get("displayName") == _SCHEMA_SCRIPT_NAME and rs.get("version") == version:
                script_id = rs.get("id")
                break
        if not script_id:
            for rs in registered:
                if rs.get("displayName") == _SCHEMA_SCRIPT_NAME:
                    script_id, version = rs.get("id"), rs.get("version", version)
                    break
        if not script_id:
            return {"status": "failed", "message": f"Couldn't register the schema script in Webflow: {exc}"}

    # 2) Merge into the site's applied custom code and upsert.
    existing = client.get_site_custom_code(site_id)
    merged = [s for s in existing if s.get("id") != script_id]
    merged.append({"id": script_id, "location": "header", "version": version})
    try:
        client.apply_site_custom_code(site_id, merged)
    except WebflowError as exc:
        if _is_scope_error(exc):
            return {"status": "failed", "message": _CUSTOM_CODE_SCOPE_MSG}
        return {"status": "failed", "message": f"Couldn't apply schema to Webflow: {exc}"}

    # 3) Publish so it goes live.
    published = _safe_publish(client, site_id)
    tail = " and published." if published else " (publish is rate-limited; it will go live on your next publish)."

    # Summarize the schema @type(s) for the change log.
    types: list[str] = []
    try:
        obj = _json.loads(jsonld)
        graph = obj.get("@graph") if isinstance(obj, dict) else None
        items = graph if isinstance(graph, list) else [obj]
        for it in items:
            t = it.get("@type") if isinstance(it, dict) else None
            if isinstance(t, list):
                types.extend(str(x) for x in t)
            elif t:
                types.append(str(t))
    except (ValueError, TypeError, AttributeError):
        pass
    changes = f"Injected JSON-LD schema ({', '.join(dict.fromkeys(types))})" if types else "Injected JSON-LD schema into <head>"

    return {
        "status": "success",
        "message": f"Injected JSON-LD schema into your Webflow site head{tail}",
        "changed_url": url,
        "changes": changes,
    }
