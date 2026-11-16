"""
Read-only chat tools for "where does everything stand": drafts waiting for the
owner and what was sent, outreach replies, guest posts, connected tools, and
one compact overview of all of it. Same rules as ``read_tools``: built from the
brand's real data, scoped to the program, nothing taken from the model.
Recipients are shown by site domain only, never by address.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from django.utils import timezone

from ...models import GrowthAction, GrowthProgram, OutreachThread
from ..control.actions import present
from ..execution.publishers.resolver import connected_platform
from ..guest_posts import report as guest_report
from ..indexing import bing
from ..indexing import report as indexing_report
from ..outreach.contact_finders import accounts
from ..outreach.mailbox import gmail_integration, mailbox_address
from ..planning.plan import active_cycle
from ..social import check as social_check

PENDING = (GrowthAction.State.AWAITING_APPROVAL, GrowthAction.State.APPROVED)
RESULT_DAYS = 14
MAX_PENDING = 15
MAX_RESULTS = 10
MAX_THREADS = 15
PREVIEW_CHARS = 160


def _actions(program: GrowthProgram):
    return GrowthAction.objects.filter(organization=program.organization).select_related(
        "current_version", "task"
    )


def _summary(action: GrowthAction) -> dict:
    shown = present(action, action.current_version) if action.current_version else {}
    text = shown.get("subject") or shown.get("title") or ""
    body = shown.get("message") or shown.get("text") or shown.get("answer") or ""
    return {
        "id": action.public_id,
        "kind": action.get_kind_display(),
        "task": action.task.title if action.task_id else "",
        "state": action.get_state_display(),
        "headline": text,
        "preview": body[:PREVIEW_CHARS],
        "to": shown.get("recipient_domain") or shown.get("site_url") or shown.get("platform") or "",
        "created": action.created_at.date().isoformat(),
    }


def get_approvals(program: GrowthProgram, _args: dict) -> dict:
    """Drafts waiting for the owner, and what happened to recent ones."""
    pending = list(_actions(program).filter(state__in=PENDING).order_by("-created_at")[:MAX_PENDING])
    since = timezone.now() - timedelta(days=RESULT_DAYS)
    recent = (
        _actions(program).exclude(state__in=PENDING).filter(updated_at__gte=since).order_by("-updated_at")
    )
    counts = Counter(a.get_state_display() for a in recent)
    return {
        "waiting_for_owner": [_summary(a) for a in pending],
        "waiting_total": _actions(program).filter(state=GrowthAction.State.AWAITING_APPROVAL).count(),
        f"last_{RESULT_DAYS}_days": dict(counts),
        "recent": [_summary(a) for a in recent[:MAX_RESULTS]],
        "where": "Approvals tab on the board (articles: Articles tab, social posts: Social tab).",
    }


def get_outreach(program: GrowthProgram, _args: dict) -> dict:
    """Outreach email threads: who replied, opted out, or is still waiting."""
    threads = OutreachThread.objects.filter(task__cycle__program=program).select_related("contact")
    counts = Counter(t.get_status_display() for t in threads)
    return {
        "threads_by_status": dict(counts),
        "threads": [
            {
                "site": t.contact.domain,
                "subject": t.subject,
                "status": t.get_status_display(),
                "follow_ups_sent": t.followups_sent,
                "next_follow_up": t.next_follow_up_on.isoformat() if t.next_follow_up_on else None,
                "replied": t.replied_at.date().isoformat() if t.replied_at else None,
            }
            for t in threads.order_by("-updated_at")[:MAX_THREADS]
        ],
        "note": "Replies are read in the brand's own Gmail; open the thread there to answer.",
    }


def get_guest_posts(program: GrowthProgram, _args: dict) -> dict:
    built = guest_report.report(program)
    return {
        "sites": [
            {
                "task_id": s["task_id"],
                "site": s["domain"],
                "why": s["reason"],
                "stage": s["stage"],
                "pitch_from": s["scheduled_for"],
            }
            for s in built["sites"]
        ],
        "monthly_cap": built["monthly_cap"],
        "searching_now": built["searching"],
        "last_search": built["last_search_at"],
        "notes": built["notes"],
        "next_step": "Pitch drafts, sent from the brand's Gmail after the owner approves each, come in the next release.",
    }


def get_connections(program: GrowthProgram, _args: dict) -> dict:
    """Which outside tools are connected, what each one enables, and where to connect it."""
    org = program.organization
    gmail = gmail_integration(org)
    finders = {f["label"]: f["connected"] for f in accounts.describe(org)}
    buffer_state = social_check.buffer_status(program)
    return {
        "gmail": {
            "connected": gmail is not None,
            "mailbox": mailbox_address(gmail),
            "enables": "sending outreach and guest-post pitches",
        },
        "contact_finders": {
            "connected": finders,
            "enables": "finding editors' emails",
            "where": "the + next to the chat box",
        },
        "buffer": {
            **buffer_state,
            "enables": "approved social posts go to the Buffer queue",
            "where": "the + next to the chat box",
        },
        "search_console": {
            "status": indexing_report.build(org)["status"],
            "where": "the Visibility or Analytics page",
        },
        "bing_weekly_sending": {"enabled": bing.enabled(program), "where": "the Indexing tab"},
        "publishing": {
            "platform": connected_platform(org) or "none",
            "enables": "publishing answer pages to the brand's own site",
        },
    }


def _plan_counts(program: GrowthProgram) -> dict:
    cycle = active_cycle(program)
    if cycle is None:
        return {"available": False}
    by_tactic = Counter((t.tactic, t.status) for t in cycle.tasks.only("tactic", "status"))
    tactics: dict[str, dict] = {}
    for (tactic, status), n in by_tactic.items():
        tactics.setdefault(tactic, {})[status] = n
    return {"from": cycle.starts_on.isoformat(), "to": cycle.ends_on.isoformat(), "tasks_by_tactic": tactics}


def get_overview(program: GrowthProgram, _args: dict) -> dict:
    """Everything at a glance, for "how is it going" or "what's running" questions."""
    approvals = _actions(program).filter(state=GrowthAction.State.AWAITING_APPROVAL)
    guest = guest_report.report(program)
    social = social_check.report(program)
    threads = OutreachThread.objects.filter(task__cycle__program=program)
    return {
        "program_status": program.status,
        "plan": _plan_counts(program),
        "drafts_waiting_for_owner": dict(Counter(a.get_kind_display() for a in approvals)),
        "sends_with_unknown_outcome": _actions(program)
        .filter(state=GrowthAction.State.NEEDS_RECONCILIATION)
        .count(),
        "outreach_threads": dict(Counter(t.get_status_display() for t in threads)),
        "guest_post_sites": dict(Counter(s["stage"] for s in guest["sites"])),
        "social_score": (social.get("overall") or {}).get("score"),
        "connections": get_connections(program, {}),
    }
