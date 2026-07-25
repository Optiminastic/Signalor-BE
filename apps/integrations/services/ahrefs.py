"""
Ahrefs API v3 client — real Domain Rating + backlink metrics (PAID).

Auth: ``Authorization: Bearer <AHREFS_API_TOKEN>``. Each call consumes Ahrefs API
units, so the ``domain_authority`` service caches results per domain. Gated on the
token: without it ``fetch_domain_authority`` raises :class:`AhrefsNotConfigured`
and the caller falls back to the free Open PageRank metric (DR only, no backlinks).

SOLID notes:
  * SRP — one job: talk to Ahrefs and return normalized authority numbers.
  * Dependency Inversion — callers depend on ``fetch_domain_authority`` /
    ``is_configured``, not on the HTTP client. Swap providers by adding a sibling
    module with the same shape.

Endpoints used (Site Explorer):
    GET /site-explorer/domain-rating?target=&date=       -> domain_rating.domain_rating (0-100)
    GET /site-explorer/backlinks-stats?target=&mode=&date= -> metrics.live, metrics.live_refdomains
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("apps")

TIMEOUT_SECONDS = 20
# Whole-domain view (apex + subdomains), matching Ahrefs' Website Authority Checker.
BACKLINK_MODE = "subdomains"


class AhrefsNotConfigured(RuntimeError):
    """Raised when AHREFS_API_TOKEN is unset — caller falls back to a free source."""


class AhrefsError(RuntimeError):
    """Raised when the Ahrefs API returns a non-success or malformed response."""


def is_configured() -> bool:
    """True when a token is set — lets callers pick Ahrefs over the free fallback."""
    return bool((getattr(settings, "AHREFS_API_TOKEN", "") or "").strip())


def _token() -> str:
    token = (getattr(settings, "AHREFS_API_TOKEN", "") or "").strip()
    if not token:
        raise AhrefsNotConfigured("AHREFS_API_TOKEN env var is not set.")
    return token


def _base_url() -> str:
    return (getattr(settings, "AHREFS_API_BASE_URL", "") or "https://api.ahrefs.com/v3").rstrip("/")


def _get(path: str, params: dict) -> dict:
    from apps.integrations._http import request_with_retry

    try:
        resp = request_with_retry(
            "GET",
            f"{_base_url()}{path}",
            params=params,
            headers={"Authorization": f"Bearer {_token()}", "Accept": "application/json"},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise AhrefsError(f"{path}: request failed: {exc}") from exc

    if not resp.ok:
        raise AhrefsError(f"{path}: HTTP {resp.status_code} from Ahrefs.")
    try:
        body = resp.json()
    except ValueError as exc:
        raise AhrefsError(f"{path}: non-JSON response from Ahrefs.") from exc
    return body if isinstance(body, dict) else {}


def _as_number(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    num = _as_number(value)
    return int(num) if num is not None else None


def fetch_domain_authority(domain: str) -> dict:
    """
    Real DR + backlink metrics for a bare domain (PAID; consumes API units).

    Returns ``{"domain_rating": int|None, "backlinks": int|None,
    "linking_websites": int|None}``. Missing fields come back as ``None`` so the
    card can hide a row rather than show a misleading 0.

    Raises :class:`AhrefsNotConfigured` if no token, or :class:`AhrefsError` on an
    HTTP / parse failure.
    """
    today = timezone.now().date().isoformat()

    dr_body = _get("/site-explorer/domain-rating", {"target": domain, "date": today})
    dr_node = dr_body.get("domain_rating")
    dr = _as_number(dr_node.get("domain_rating")) if isinstance(dr_node, dict) else None

    bl_body = _get(
        "/site-explorer/backlinks-stats",
        {"target": domain, "mode": BACKLINK_MODE, "date": today},
    )
    metrics = bl_body.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}

    return {
        "domain_rating": round(dr) if dr is not None else None,
        "backlinks": _as_int(metrics.get("live")),
        "linking_websites": _as_int(metrics.get("live_refdomains")),
    }
