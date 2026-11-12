"""Hunter.io Domain Search: people at a domain, with their emails and a confidence score.

Docs: https://hunter.io/api-documentation/v2#domain-search
The key is sent in the X-API-KEY header, never in the URL.
"""

from __future__ import annotations

import requests

from .base import TIMEOUT_SECONDS, Candidate, FinderError

PROVIDER = "hunter"
DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"
# Departments that usually handle editorial and partnership pitches.
DEPARTMENTS = "marketing,communication,management,executive"
LIMIT = 10  # the free plan's maximum per search


def _candidate(item: dict) -> Candidate | None:
    email = item.get("value") or ""
    name = " ".join(part for part in (item.get("first_name"), item.get("last_name")) if part)
    if not email or item.get("type") == "generic" or not name:
        return None  # a shared inbox (info@, hello@) isn't a person to pitch
    confidence = item.get("confidence")
    return Candidate(
        provider=PROVIDER,
        ref=email,
        name=name,
        title=item.get("position") or "",
        email=email,
        confidence=f"{confidence}%" if confidence is not None else "",
    )


def find(domain: str, api_key: str) -> list[Candidate]:
    try:
        reply = requests.get(
            DOMAIN_SEARCH_URL,
            params={"domain": domain, "limit": LIMIT, "type": "personal", "department": DEPARTMENTS},
            headers={"X-API-KEY": api_key},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise FinderError(f"Hunter couldn't be reached: {type(exc).__name__}") from exc
    if not reply.ok:
        raise FinderError(f"Hunter answered HTTP {reply.status_code}")
    emails = (reply.json().get("data") or {}).get("emails") or []
    return [c for c in (_candidate(item) for item in emails) if c is not None]
