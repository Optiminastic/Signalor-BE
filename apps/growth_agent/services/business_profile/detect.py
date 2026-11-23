"""
Does the brand have a Google Business Profile (its Google Maps / search listing)?

One Google Places search through Serper for the brand's name (plus the owner's
city or address when known). A listing is the brand's when:
- its website is the brand's domain, or
- its name matches the brand's name and the owner's address appears in it.

A failed search is "unknown", never "not found": the agent must not tell an
owner they have no listing because a search failed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import requests

from core.llm import serper

from ..positioning.inputs import bare_domain

logger = logging.getLogger("apps")

PLACES_URL = "https://google.serper.dev/places"
TIMEOUT_SECONDS = 15
MAPS_URL = "https://www.google.com/maps?cid={cid}"
_WORDS = re.compile(r"[a-z0-9]+")
NAME_OVERLAP = 0.6  # share of the brand name's words that must appear in the listing name


@dataclass(frozen=True)
class Detection:
    status: str  # found | not_found | unknown
    place: dict = field(default_factory=dict)
    matched_by: str = ""


def _words(text: str) -> set[str]:
    return {w for w in _WORDS.findall((text or "").lower()) if len(w) >= 2}


def _same_site(website: str, domain: str) -> bool:
    return bool(website and domain) and bare_domain(website) == domain


def _same_name(listing: str, brand: str) -> bool:
    wanted = _words(brand)
    return bool(wanted) and len(wanted & _words(listing)) / len(wanted) >= NAME_OVERLAP


def _same_address(listing_address: str, owner_address: str) -> bool:
    wanted = _words(owner_address)
    return bool(wanted) and len(wanted & _words(listing_address)) / len(wanted) >= NAME_OVERLAP


def _summary(place: dict) -> dict:
    cid = str(place.get("cid") or "")
    return {
        "name": place.get("title", ""),
        "address": place.get("address", ""),
        "category": place.get("category", ""),
        "website": place.get("website", ""),
        "phone": place.get("phoneNumber", ""),
        "rating": place.get("rating"),
        "reviews": place.get("ratingCount"),
        "cid": cid,
        "link": MAPS_URL.format(cid=cid) if cid else "",
    }


def _places(query: str) -> list[dict] | None:
    key = serper.api_key()
    if not key:
        return None
    try:
        reply = requests.post(
            PLACES_URL,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": query},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        logger.warning("business profile: places search failed")
        return None
    if not reply.ok:
        logger.warning("business profile: places search HTTP %s", reply.status_code)
        return None
    try:
        return reply.json().get("places") or []
    except ValueError:
        return None


def match(places: list[dict], *, brand: str, domain: str, address: str = "") -> Detection:
    """Pick the brand's listing from search results. Pure."""
    for place in places:
        if _same_site(place.get("website", ""), domain):
            return Detection("found", _summary(place), "website")
    for place in places:
        if _same_name(place.get("title", ""), brand) and _same_address(place.get("address", ""), address):
            return Detection("found", _summary(place), "name_address")
    return Detection("not_found")


def detect(*, brand: str, website: str, address: str = "") -> Detection:
    """Search Google Places and decide. Network."""
    query = f"{brand} {address}".strip()
    places = _places(query)
    if places is None:
        return Detection("unknown")
    return match(places, brand=brand, domain=bare_domain(website or ""), address=address)
