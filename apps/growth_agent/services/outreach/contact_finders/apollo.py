"""Apollo.io: people search by company domain, then reveal one person's work email.

Docs: https://docs.apollo.io/reference/people-api-search and
https://docs.apollo.io/reference/people-enrichment
- Search costs 0 credits and returns no emails.
- Revealing a work email (people/match) costs 1 credit, so it runs only for
  the person the owner picks.
The key is sent in the x-api-key header.
Not yet verified against a live Apollo account.
"""

from __future__ import annotations

import requests

from .base import PITCH_TITLES, TIMEOUT_SECONDS, Candidate, FinderError

PROVIDER = "apollo"
SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
MATCH_URL = "https://api.apollo.io/api/v1/people/match"
PER_PAGE = 10


def _post(url: str, api_key: str, body: dict) -> dict:
    try:
        reply = requests.post(
            url,
            json=body,
            headers={"x-api-key": api_key, "Content-Type": "application/json", "Cache-Control": "no-cache"},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise FinderError(f"Apollo couldn't be reached: {type(exc).__name__}") from exc
    if not reply.ok:
        raise FinderError(f"Apollo answered HTTP {reply.status_code}")
    return reply.json()


def find(domain: str, api_key: str) -> list[Candidate]:
    body = {
        "q_organization_domains_list": [domain],
        "person_titles": list(PITCH_TITLES),
        "include_similar_titles": True,
        "per_page": PER_PAGE,
    }
    people = _post(SEARCH_URL, api_key, body).get("people") or []
    candidates = []
    for person in people:
        if not person.get("id") or not person.get("has_email"):
            continue  # no email to reveal: not worth a credit
        name = " ".join(p for p in (person.get("first_name"), person.get("last_name_obfuscated")) if p)
        candidates.append(Candidate(PROVIDER, person["id"], name, person.get("title") or "", "", ""))
    return candidates


def reveal(person_id: str, api_key: str) -> str:
    """The person's work email. Costs 1 Apollo credit when an email is found."""
    person = _post(MATCH_URL, api_key, {"id": person_id}).get("person") or {}
    email = person.get("email") or ""
    if not email:
        raise FinderError("Apollo has no work email for this person.")
    return email
