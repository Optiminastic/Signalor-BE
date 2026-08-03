"""Module-level helpers and constants shared across the views modules.

Extracted verbatim from the original 2,723-line views.py.
"""

import hashlib
import hmac
import json
import logging
import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from django.conf import settings
from django.http import HttpResponseRedirect
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from rest_framework import status
from rest_framework.response import Response

from apps.organizations.models import Organization

from ..models import (
    Integration,
)

logger = logging.getLogger("apps")
_SHOPIFY_OAUTH_CALLBACK_PATH = "/api/integrations/shopify/callback/"
GA_SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
]
GSC_SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
]
_ALLOWED_RANGE_DAYS = {7, 14, 30, 90}

def _resolve_shopify_redirect_uri(request) -> str:
    """
    OAuth redirect_uri must exactly match a URL in the Shopify app allowlist.

    Resolution order for non-local requests:
    1. SHOPIFY_REDIRECT_URI_PUBLIC — canonical HTTPS callback when Host/build_absolute_uri
       is wrong behind some proxies or internal hostnames.
    2. SHOPIFY_REDIRECT_URI — if set and not localhost-only while request is public,
       use request.build_absolute_uri (so allowlist gets the real public URL).
    3. If SHOPIFY_REDIRECT_URI is localhost-only but the request is not local,
       use build_absolute_uri (avoids sending localhost redirect from prod).

    Local requests: SHOPIFY_REDIRECT_URI if set, else build_absolute_uri.
    """
    built = request.build_absolute_uri(_SHOPIFY_OAUTH_CALLBACK_PATH)
    explicit = os.getenv("SHOPIFY_REDIRECT_URI", "").strip()
    public = os.getenv("SHOPIFY_REDIRECT_URI_PUBLIC", "").strip()
    host = (request.get_host() or "").lower().split(":")[0]
    is_local_req = host in ("localhost", "127.0.0.1")
    explicit_mentions_local = bool(explicit and ("localhost" in explicit or "127.0.0.1" in explicit))

    def _with_slash(u: str) -> str:
        u = u.strip()
        return u if u.endswith("/") else f"{u}/"

    if not is_local_req and public:
        return _with_slash(public)

    if not explicit:
        return built

    if explicit_mentions_local and not is_local_req:
        return built

    return _with_slash(explicit)

def _get_org_or_400(email):
    """Return the caller's existing org, or a 404 response when they have none.

    IMPORTANT: this never creates an organization. Brands/orgs are created ONLY
    during onboarding (apps/organizations/serializers.py). Auto-creating one here
    caused the "Individual accounts include a single brand" bug: a status poll
    (IntegrationStatusView) would silently create the user's first org right after
    sign-in, so by the time onboarding reached the URL step the project limit was
    already used up. A read must never have this side effect.
    """
    org = (
        Organization.objects.filter(owner_email=(email or "").lower().strip())
        .order_by("id")
        .first()
    )
    if org:
        return org, None
    return None, Response(
        {"error": "No brand found for this account. Complete onboarding first."},
        status=status.HTTP_404_NOT_FOUND,
    )

def _org_id_param(request) -> int | None:
    """The selected brand's org id from the query string or body, if numeric.

    Every brand-scoped endpoint should pass this to ``_resolve_org``. Without it
    the caller silently falls back to the account's FIRST brand, which is what
    made one brand's Integrations page show another brand's connections.
    """
    raw = request.query_params.get("org_id") or ""
    if not raw and hasattr(request, "data") and isinstance(request.data, dict):
        raw = request.data.get("org_id") or ""
    raw = str(raw).strip()
    return int(raw) if raw.isdigit() else None

def _requested_days(request) -> int:
    """The explicit analytics window from ?days=, or 0 to use the cached snapshot."""
    raw = (request.query_params.get("days") or "").strip()
    if raw.isdigit() and int(raw) in _ALLOWED_RANGE_DAYS:
        return int(raw)
    return 0

def _append_query_params(url: str, extra: dict[str, str]) -> str:
    """Add query keys only if not already present (preserve Shopify install signatures)."""
    parts = urlparse(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    existing_keys = {k for k, _ in pairs}
    for key, value in extra.items():
        if key not in existing_keys:
            pairs.append((key, value))
    new_query = urlencode(pairs)
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, new_query, parts.fragment))

def _resolve_org(email: str, org_id: int | None = None):
    """
    Resolve org by id (preferred) or by email.

    If ``org_id`` is given but doesn't match an existing row, return 404 — do
    NOT silently fall through to email lookup. The previous fallback
    auto-created a new org for the caller, which produced orphan rows and
    masked client bugs (e.g. stale org IDs in the URL).
    """
    email_norm = email.lower().strip()
    if org_id:
        try:
            org = Organization.objects.get(pk=org_id)
        except Organization.DoesNotExist:
            return None, Response(
                {"error": "Organization not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if (org.owner_email or "").lower().strip() != email_norm:
            return None, Response(
                {"error": "Organization does not belong to this account."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return org, None
    return _get_org_or_400(email)

def _sign_state(payload: dict) -> str:
    """HMAC-sign a JSON state payload."""
    raw = json.dumps(payload, sort_keys=True)
    sig = hmac.new(settings.SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return json.dumps({"data": payload, "sig": sig})

def _verify_state(state_str: str) -> dict | None:
    """Verify HMAC signature and return payload, or None if invalid."""
    try:
        state = json.loads(state_str)
        raw = json.dumps(state["data"], sort_keys=True)
        expected = hmac.new(settings.SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, state["sig"]):
            return state["data"]
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    return None

def _deactivate_other_store_integration(org: Organization, keep_provider: str) -> None:
    """
    Only one store platform (WordPress or Shopify) may be active per organization.
    When connecting `keep_provider`, deactivate the other integration row if present.
    """
    if keep_provider not in (
        Integration.Provider.SHOPIFY,
        Integration.Provider.WORDPRESS,
    ):
        return
    other = (
        Integration.Provider.WORDPRESS
        if keep_provider == Integration.Provider.SHOPIFY
        else Integration.Provider.SHOPIFY
    )
    n = Integration.objects.filter(
        organization=org,
        provider=other,
        is_active=True,
    ).update(is_active=False)
    if n:
        logger.info(
            "Deactivated %s for org %s; %s is now the active store.",
            other,
            org.id,
            keep_provider,
        )

def _redirect_with_status(
    ok: bool,
    reason: str = "",
    return_to: str = "/settings/integrations",
    provider: str = "wordpress",
):
    """Build a redirect to the frontend with a status query param."""
    frontend_base = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")
    target = (
        return_to
        if return_to.startswith("/") and not return_to.startswith("//")
        else "/settings/integrations"
    )
    sep = "&" if "?" in target else "?"
    status_q = "connected" if ok else "error"
    url = f"{frontend_base}{target}{sep}{urlencode({provider: status_q, 'reason': reason})}"
    return HttpResponseRedirect(url)

def _build_credentials(integration: Integration, scopes: list[str] | None = None) -> Credentials:
    """Build google.oauth2.credentials.Credentials from an Integration."""
    return Credentials(
        token=integration.get_access_token(),
        refresh_token=integration.get_refresh_token(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=scopes or GA_SCOPES,
    )

def _refresh_if_needed(integration: Integration, creds: Credentials) -> Credentials:
    if not creds.refresh_token:
        return creds

    needs_refresh = creds.expiry is None or creds.expired
    if needs_refresh:
        try:
            creds.refresh(GoogleRequest())
            integration.set_access_token(creds.token)
            if creds.refresh_token:
                integration.set_refresh_token(creds.refresh_token)
            integration.save(
                update_fields=[
                    "access_token_encrypted",
                    "refresh_token_encrypted",
                    "updated_at",
                ]
            )
        except Exception as exc:
            logger.warning("Token refresh failed: %s", exc)
            raise
    return creds

def _gsc_redirect(ok: bool, frontend_base: str, return_to: str, reason: str = ""):
    """Redirect the browser back to the frontend after the GSC OAuth callback."""
    base = (frontend_base or os.getenv("FRONTEND_BASE_URL", "http://localhost:3000")).rstrip("/")
    safe_return = return_to if (return_to.startswith("/") and not return_to.startswith("//")) else "/"
    sep = "&" if "?" in safe_return else "?"
    status_q = f"gsc={'connected' if ok else 'error'}"
    if not ok and reason:
        status_q += f"&reason={reason}"
    return HttpResponseRedirect(f"{base}{safe_return}{sep}{status_q}")

