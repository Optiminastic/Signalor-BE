"""AnalysisRun -> Slack Block Kit.

Pure: no Django ORM writes, no network, no settings lookups beyond the values
passed in. That keeps the message layout unit-testable without a workspace, and
means a formatting change can never break the delivery path.
"""

from __future__ import annotations

# Slack renders at most 50 blocks; we stay far below, but the task list is the
# one unbounded input so it is capped explicitly.
MAX_TASKS_SHOWN = 5

# Score bands, mirroring the dashboard's scoreColor thresholds.
_GOOD = 70
_FAIR = 40


def _score_emoji(score: float) -> str:
    if score >= _GOOD:
        return ":large_green_circle:"
    if score >= _FAIR:
        return ":large_yellow_circle:"
    return ":red_circle:"


def _trend(delta: float | None) -> str:
    """"+4 since last run" — omitted entirely when there is no prior run."""
    if delta is None:
        return ""
    if delta > 0:
        return f"  :arrow_upper_right: +{delta:.0f} since last run"
    if delta < 0:
        return f"  :arrow_lower_right: {delta:.0f} since last run"
    return "  no change since last run"


def _task_line(task: dict) -> str:
    """One task as a bullet. ``task`` is a plain dict so this stays ORM-free."""
    title = (task.get("title") or "Untitled task").strip()
    signal = (task.get("signal") or "").strip()
    priority = (task.get("priority") or "").strip().title()
    suffix = " · ".join(p for p in (priority, signal) if p)
    return f"• *{title}*{f'  _{suffix}_' if suffix else ''}"


def analysis_complete_blocks(
    *,
    brand: str,
    url: str,
    score: float,
    delta: float | None,
    tasks: list[dict],
    dashboard_url: str,
) -> list[dict]:
    """The message posted when a run finishes.

    ``tasks`` are dicts (title/signal/priority), not model instances, so the
    caller decides what to surface and this module never touches the database.
    """
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"GEO analysis complete — {brand}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{_score_emoji(score)}  *{score:.0f}/100* GEO score{_trend(delta)}\n"
                    f"<{url}|{url}>"
                ),
            },
        },
    ]

    if tasks:
        shown = tasks[:MAX_TASKS_SHOWN]
        more = len(tasks) - len(shown)
        lines = "\n".join(_task_line(t) for t in shown)
        if more > 0:
            lines += f"\n_and {more} more_"
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Top actions*\n{lines}"},
            }
        )

    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View full report"},
                    "url": dashboard_url,
                    "style": "primary",
                }
            ],
        }
    )
    return blocks
