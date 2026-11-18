"""
Keyword providers a brand can connect with its own API key: Semrush and Ahrefs.

Each offers ``check_key(key)`` (a free call, used before a key is saved) and
``ranked_keywords(domain, key)`` returning rows in the same shape as
DataForSEO's: {keyword, position, search_volume, url}. Keys are never logged.

Semrush: https://api.semrush.com/?type=domain_organic (CSV, ";" separated,
10 units a line); balance check at /users/countapiunits.html (free).
Ahrefs:  GET /v3/site-explorer/organic-keywords (JSON "keywords");
key check at /v3/subscription-info/limits-and-usage (free).
"""

from __future__ import annotations

import csv
import io

import requests
from django.utils import timezone

TIMEOUT_SECONDS = 20
LIMIT = 100
SEMRUSH = "semrush"
AHREFS = "ahrefs"
SEMRUSH_API = "https://api.semrush.com/"
SEMRUSH_UNITS = "https://www.semrush.com/users/countapiunits.html"
SEMRUSH_DATABASE = "us"
AHREFS_API = "https://api.ahrefs.com/v3"
AHREFS_COUNTRY = "us"


class ProviderError(Exception):
    """The provider refused or failed; the message is safe to show the owner."""


def _get(url: str, *, params: dict | None = None, headers: dict | None = None) -> requests.Response:
    try:
        return requests.get(url, params=params, headers=headers, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise ProviderError("The provider didn't answer. Try again in a moment.") from exc


# ── Semrush ────────────────────────────────────────────────────────────────


def semrush_check_key(key: str) -> None:
    """The unit balance is a plain number for a valid key; anything else is refused."""
    body = _get(SEMRUSH_UNITS, params={"key": key}).text.strip()
    if not body.replace(",", "").isdigit():
        raise ProviderError("Semrush didn't accept that API key.")


def semrush_rows(text: str) -> list[dict]:
    """Parse Semrush's CSV (Keyword;Position;Search Volume;Url)."""
    if text.strip().upper().startswith("ERROR"):
        raise ProviderError("Semrush refused the request. Check the API key and its units.")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    return [
        {
            "keyword": row.get("Keyword", ""),
            "position": int(row.get("Position") or 0),
            "search_volume": int(row.get("Search Volume") or 0),
            "url": row.get("Url", ""),
        }
        for row in reader
        if row.get("Keyword")
    ]


def semrush_ranked_keywords(domain: str, key: str) -> list[dict]:
    params = {
        "type": "domain_organic",
        "key": key,
        "domain": domain,
        "database": SEMRUSH_DATABASE,
        "display_limit": LIMIT,
        "display_sort": "tr_desc",
        "export_columns": "Ph,Po,Nq,Ur",
    }
    return semrush_rows(_get(SEMRUSH_API, params=params).text)


# ── Ahrefs ─────────────────────────────────────────────────────────────────


def _ahrefs_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


def ahrefs_check_key(key: str) -> None:
    reply = _get(f"{AHREFS_API}/subscription-info/limits-and-usage", headers=_ahrefs_headers(key))
    if reply.status_code != 200:
        raise ProviderError("Ahrefs didn't accept that API key.")


def ahrefs_rows(payload: dict) -> list[dict]:
    return [
        {
            "keyword": item.get("keyword") or "",
            "position": int(item.get("best_position") or 0),
            "search_volume": int(item.get("volume") or 0),
            "url": item.get("best_position_url") or "",
        }
        for item in (payload or {}).get("keywords") or []
        if item.get("keyword")
    ]


def ahrefs_ranked_keywords(domain: str, key: str) -> list[dict]:
    params = {
        "target": domain,
        "date": timezone.now().date().isoformat(),
        "country": AHREFS_COUNTRY,
        "mode": "subdomains",
        "select": "keyword,best_position,volume,best_position_url,sum_traffic",
        "order_by": "sum_traffic:desc",
        "limit": LIMIT,
    }
    reply = _get(f"{AHREFS_API}/site-explorer/organic-keywords", params=params, headers=_ahrefs_headers(key))
    if reply.status_code != 200:
        raise ProviderError(f"Ahrefs refused the request (HTTP {reply.status_code}).")
    return ahrefs_rows(reply.json())


CHECKS = {SEMRUSH: semrush_check_key, AHREFS: ahrefs_check_key}
FETCHERS = {SEMRUSH: semrush_ranked_keywords, AHREFS: ahrefs_ranked_keywords}
LABELS = {SEMRUSH: "Semrush", AHREFS: "Ahrefs"}
