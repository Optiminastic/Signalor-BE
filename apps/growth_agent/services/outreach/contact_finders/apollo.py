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
    return candidates_from(_post(SEARCH_URL, api_key, search_body(domain)))


def search_body(domain: str) -> dict:
    return {
        "q_organization_domains_list": [domain],
        "person_titles": list(PITCH_TITLES),
        "include_similar_titles": True,
        "per_page": PER_PAGE,
    }


def candidates_from(payload: object) -> list[Candidate]:
    """People worth a reveal, from a people search (REST API or MCP tool)."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    candidates = []
    for person in data.get("people") or []:
        if not isinstance(person, dict) or not person.get("id") or person.get("has_email") is False:
            continue  # no email to reveal: not worth a credit
        last = person.get("last_name_obfuscated") or person.get("last_name")
        name = " ".join(p for p in (person.get("first_name"), last) if p)
        candidates.append(Candidate(PROVIDER, str(person["id"]), name, person.get("title") or "", "", ""))
    return candidates


def email_from(payload: object) -> str:
    """The work email in a people/match answer (REST API or MCP tool)."""
    if not isinstance(payload, dict):
        return ""
    person = payload.get("person") if isinstance(payload.get("person"), dict) else payload
    return person.get("email") or ""


def check_key(api_key: str) -> None:
    """Raise FinderError unless Apollo accepts the key. A one-result search costs no credits."""
    try:
        _post(SEARCH_URL, api_key, {"q_organization_domains_list": ["apollo.io"], "per_page": 1})
    except FinderError as exc:
        raise FinderError("Apollo didn't accept this API key.") from exc


def reveal(person_id: str, api_key: str) -> str:
    """The person's work email. Costs 1 Apollo credit when an email is found."""
    email = email_from(_post(MATCH_URL, api_key, {"id": person_id}))
    if not email:
        raise FinderError("Apollo has no work email for this person.")
    return email
