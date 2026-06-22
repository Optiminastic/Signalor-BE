"""
Open PageRank API client.

Free, Common Crawl–based domain authority metric (a Moz DA / Ahrefs DR
alternative). Powers the public Domain Rating tool without paid credits.

Auth: a single API key sent in the ``API-OPR`` header (free key from
domcop.com/openpagerank). Free tier allows 10,000 calls/hour.

Endpoint used:
    GET /api/v1.0/getPageRank?domains[]=<domain>  -> page_rank_decimal (0-10), rank (global)
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger("apps")

API_URL = "https://openpagerank.com/api/v1.0/getPageRank"
TIMEOUT_SECONDS = 15
OPR_OK_STATUS = 200


class OpenPageRankNotConfigured(RuntimeError):
    """Raised when OPENPAGERANK_API_KEY is missing from settings."""


class OpenPageRankError(RuntimeError):
    """Raised when Open PageRank returns a non-success response."""


def _api_key() -> str:
    key = getattr(settings, "OPENPAGERANK_API_KEY", "") or ""
    if not key:
        raise OpenPageRankNotConfigured("OPENPAGERANK_API_KEY env var is not set.")
    return key


def _to_int(value) -> int | None:
    """Open PageRank returns ``rank`` as a numeric string, null, or '0'/''."""
    try:
        rank = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return rank if rank > 0 else None


def fetch_page_rank(domain: str) -> dict:
    """
    Fetch the Open PageRank authority metrics for a single bare domain.

    Returns ``{"page_rank_decimal": float, "global_rank": int | None, "found": bool}``.
    ``found`` is False when the domain isn't in the Open PageRank index yet
    (callers surface a "no data" state rather than a misleading 0).

    Raises ``OpenPageRankNotConfigured`` if no API key is set, or
    ``OpenPageRankError`` on an HTTP / envelope failure.
    """
    from apps.integrations._http import request_with_retry

    try:
        resp = request_with_retry(
            "GET",
            API_URL,
            params={"domains[]": domain},
            headers={"API-OPR": _api_key()},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        # Transport-level failure (DNS, connection, timeout). Translate to our
        # error so the view returns a clean 502 JSON instead of a 500 HTML page.
        raise OpenPageRankError(f"getPageRank: request failed: {exc}") from exc

    if not resp.ok:
        raise OpenPageRankError(f"getPageRank: HTTP {resp.status_code} from Open PageRank.")

    try:
        body = resp.json()
    except ValueError as exc:
        raise OpenPageRankError("getPageRank: non-JSON response from Open PageRank.") from exc
    if body.get("status_code") != OPR_OK_STATUS:
        raise OpenPageRankError(
            f"getPageRank: {body.get('status_code')} {body.get('error') or ''}".strip()
        )

    rows = body.get("response") or []
    if not rows:
        return {"page_rank_decimal": 0.0, "global_rank": None, "found": False}

    row = rows[0]
    # status_code 200 = found; 404 = domain not in the index (no data yet).
    found = row.get("status_code") == OPR_OK_STATUS
    try:
        decimal = float(row.get("page_rank_decimal") or 0)
    except (TypeError, ValueError):
        decimal = 0.0

    return {
        "page_rank_decimal": decimal,
        "global_rank": _to_int(row.get("rank")),
        "found": found,
    }
