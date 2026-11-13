"""The weekly "Request indexing" list: pages worth asking Google about, as links.

Google has no API for Search Console's "Request indexing" button, and driving
Search Console with the owner's login would break Google's terms. So the agent
prepares the list and a deep link per page into Search Console's URL
inspection; the owner presses "Request indexing" there.

- Only pages where a request can help: Google knows of them or crawled them
  but hasn't indexed them. Pages blocked by noindex, robots.txt, redirects or
  404s need a fix first; asking before that is wasted.
- At most ``WEEKLY_LIMIT`` a week (Google caps manual requests per property).
- A page the owner requested isn't offered again for ``REQUEST_COOLDOWN``.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

from django.utils import timezone

from ...models import AgentActionLog, GrowthProgram
from ..execution import audit
from .reasons import classify

WEEKLY_LIMIT = 10
REQUEST_COOLDOWN = timedelta(days=7)
REQUESTED = "indexing_requested"
# Reasons where "Request indexing" can help on its own.
REQUESTABLE = ("discovered_not_indexed", "crawled_not_indexed", "unknown", "other")
INSPECT_URL = "https://search.google.com/search-console/inspect"


def inspect_link(site_url: str, page_url: str) -> str:
    """Search Console's URL inspection for one page, where "Request indexing" is."""
    return f"{INSPECT_URL}?resource_id={quote(site_url, safe='')}&id={quote(page_url, safe='')}"


def recently_requested(program: GrowthProgram) -> dict[str, object]:
    """URL -> when the owner last opened its request link, within the cooldown."""
    since = timezone.now() - REQUEST_COOLDOWN
    entries = AgentActionLog.objects.filter(program=program, action=REQUESTED, created_at__gte=since)
    requested: dict[str, object] = {}
    for entry in entries.order_by("created_at"):
        requested[entry.detail.get("url", "")] = entry.created_at
    return requested


def weekly_list(program: GrowthProgram | None, site_url: str, pages: list[dict]) -> dict:
    """This week's pages to request, and the ones already requested recently."""
    requested = recently_requested(program) if program else {}
    to_request, done = [], []
    for page in pages:
        if page.get("on_google"):
            continue
        reason = classify(page.get("coverage_state", ""))
        if reason.key not in REQUESTABLE:
            continue
        url = page.get("url", "")
        item = {"url": url, "reason": reason.label, "inspect_url": inspect_link(site_url, url)}
        if url in requested:
            done.append({**item, "requested_at": requested[url]})
        elif len(to_request) < WEEKLY_LIMIT:
            to_request.append(item)
    return {"to_request": to_request, "requested": done}


def mark_requested(program: GrowthProgram, url: str, actor_email: str) -> None:
    audit.record(
        program, None, REQUESTED, AgentActionLog.Outcome.SUCCEEDED, {"url": url}, actor_email=actor_email
    )
