"""Send new and changed pages to Bing every week (IndexNow), once the owner turns it on.

Bing's index feeds ChatGPT search. IndexNow tells Bing, Yandex, Seznam and
Naver that pages changed; it publishes nothing on the site, so the owner turns
it on once (recorded: who and when) and the agent then runs it weekly. Bing
still decides whether to index a page: "sent" never means "indexed".

Everything is kept in the append-only Agent log, no extra table:
- ``bing_autosubmit_on`` / ``_off``: the owner's switch (latest wins).
- ``bing_send_started``: a weekly or on-demand run was queued.
- ``bing_submitted``: pages sent, with a short fingerprint of each URL so next
  week only new or changed pages go out.
- ``bing_no_changes`` / ``bing_send_blocked``: nothing to send, or why not.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone

from apps.analyzer.services import indexnow

from ...models import AgentActionLog, GrowthProgram
from ..execution import audit
from . import sitemap

ON, OFF = "bing_autosubmit_on", "bing_autosubmit_off"
STARTED, SUBMITTED = "bing_send_started", "bing_submitted"
NO_CHANGES, BLOCKED = "bing_no_changes", "bing_send_blocked"
RESULTS = (SUBMITTED, NO_CHANGES, BLOCKED)
SEND_EVERY = timedelta(days=7)
SEND_NOW_GUARD = timedelta(hours=6)
SAMPLE = 10
FINGERPRINT_CHARS = 12


def _latest(program: GrowthProgram, actions: tuple[str, ...]) -> AgentActionLog | None:
    return AgentActionLog.objects.filter(program=program, action__in=actions).order_by("-created_at").first()


def enabled(program: GrowthProgram) -> bool:
    switch = _latest(program, (ON, OFF))
    return switch is not None and switch.action == ON


def set_enabled(program: GrowthProgram, on: bool, actor_email: str) -> None:
    if on == enabled(program):
        return
    audit.record(
        program, None, ON if on else OFF, AgentActionLog.Outcome.SUCCEEDED, {}, actor_email=actor_email
    )


def site_of(program: GrowthProgram) -> str:
    url = program.organization.url or ""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return f"{parsed.scheme or 'https'}://{parsed.netloc}" if parsed.netloc else ""


def key_setup(program: GrowthProgram) -> dict:
    """The key file the site must host, so Bing trusts submissions for it."""
    key = indexnow.key_for_org(program.organization_id)
    return {"key": key, "key_file_url": indexnow.key_file_url(site_of(program), key)}


def verify(program: GrowthProgram) -> tuple[bool, str]:
    """Fetch the key file now (network; SSRF-guarded inside ``indexnow``)."""
    return indexnow.verify_key_file(site_of(program), indexnow.key_for_org(program.organization_id))


def _fingerprint(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:FINGERPRINT_CHARS]


def _sent_before(program: GrowthProgram) -> tuple[set[str], object]:
    """Fingerprints of every page sent so far, and when the last batch went out."""
    batches = AgentActionLog.objects.filter(program=program, action=SUBMITTED).order_by("created_at")
    sent: set[str] = set()
    last_at = None
    for batch in batches:
        sent.update(batch.detail.get("fingerprints") or [])
        last_at = batch.created_at
    return sent, last_at


def changed_pages(entries: list[sitemap.SitemapEntry], sent: set[str], last_at) -> list[str]:
    """New pages (never sent) and pages whose lastmod is after the last batch."""
    return [
        e.url
        for e in entries
        if _fingerprint(e.url) not in sent
        or (last_at is not None and e.lastmod is not None and e.lastmod > last_at)
    ]


def _record(
    program: GrowthProgram, action: str, detail: dict, outcome=AgentActionLog.Outcome.SUCCEEDED
) -> None:
    audit.record(program, None, action, outcome, detail)


def send(program: GrowthProgram) -> str:
    """Send this week's new and changed pages. Runs on the worker. Returns the result action."""
    entries = sitemap.fetch_entries(site_of(program))
    if not entries:
        _record(
            program,
            BLOCKED,
            {"message": f"No sitemap found for {site_of(program)}."},
            AgentActionLog.Outcome.FAILED,
        )
        return BLOCKED
    sent, last_at = _sent_before(program)
    pages = changed_pages(entries, sent, last_at)[: indexnow.MAX_URLS]
    if not pages:
        _record(program, NO_CHANGES, {"sitemap_pages": len(entries)})
        return NO_CHANGES
    site = f"{urlparse(pages[0]).scheme}://{urlparse(pages[0]).netloc}"
    result = indexnow.submit(site, pages, key=indexnow.key_for_org(program.organization_id))
    if not result.ok:
        detail = {"message": result.message, "status_code": result.status_code}
        _record(program, BLOCKED, detail, AgentActionLog.Outcome.FAILED)
        return BLOCKED
    accepted = [p for p in pages if urlparse(p).netloc == urlparse(site).netloc]
    detail = {
        "submitted": result.submitted,
        "status_code": result.status_code,
        "message": result.message,
        "sample": accepted[:SAMPLE],
        "fingerprints": [_fingerprint(p) for p in accepted],
    }
    _record(program, SUBMITTED, detail)
    return SUBMITTED


def start(program: GrowthProgram, *, guard: timedelta, actor_email: str = "") -> bool:
    """Queue a run unless one was queued within ``guard``."""
    from ...analysis_tasks import dispatch_bing_send

    last = _latest(program, (STARTED,))
    if last is not None and timezone.now() - last.created_at < guard:
        return False
    audit.record(program, None, STARTED, AgentActionLog.Outcome.SUCCEEDED, {}, actor_email=actor_email)
    transaction.on_commit(lambda: dispatch_bing_send(program.id))
    return True


def weekly(program: GrowthProgram) -> bool:
    return enabled(program) and start(program, guard=SEND_EVERY)


def _last_result(program: GrowthProgram) -> dict | None:
    entry = _latest(program, RESULTS)
    if entry is None:
        return None
    detail = entry.detail
    return {
        "kind": {SUBMITTED: "submitted", NO_CHANGES: "no_changes", BLOCKED: "blocked"}[entry.action],
        "at": entry.created_at,
        "submitted": detail.get("submitted", 0),
        "sample": detail.get("sample", []),
        "message": detail.get("message", ""),
    }


def state(program: GrowthProgram) -> dict:
    """What the Indexing tab shows for Bing. No network calls."""
    switch = _latest(program, (ON, OFF))
    started = _latest(program, (STARTED,))
    return {
        "enabled": switch is not None and switch.action == ON,
        "enabled_at": switch.created_at if switch is not None and switch.action == ON else None,
        "site": site_of(program),
        **key_setup(program),
        "last": _last_result(program),
        "next_at": started.created_at + SEND_EVERY if started is not None else None,
    }
