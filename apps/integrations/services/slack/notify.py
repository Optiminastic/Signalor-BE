"""Post an analysis report to a brand's Slack channel.

Joins `client` (transport) and `blocks` (formatting) and owns the only ORM
reads. Best-effort throughout: a Slack outage, a revoked token or a deleted
channel must never fail the analysis that triggered it.
"""

from __future__ import annotations

import logging
import os

from . import blocks as slack_blocks
from . import client as slack_client

logger = logging.getLogger("apps")

# How many recommendations to surface. The message is a nudge into the product,
# not a replacement for the task list.
TOP_TASKS = 5


def _dashboard_url(org_slug: str) -> str:
    base = os.getenv("FRONTEND_URL", "https://signalor.ai").rstrip("/")
    return f"{base}/dashboard/{org_slug}" if org_slug else base


def _integration_for(organization_id: int):
    """The org's active Slack integration, or None when not connected."""
    from apps.integrations.models import Integration

    return Integration.objects.filter(
        organization_id=organization_id,
        provider=Integration.Provider.SLACK,
        is_active=True,
    ).first()


def _previous_score(run) -> float | None:
    """The score of the run before this one, for the trend line."""
    from apps.analyzer.models import AnalysisRun

    prev = (
        AnalysisRun.objects.filter(
            organization_id=run.organization_id,
            status=AnalysisRun.Status.COMPLETE,
            created_at__lt=run.created_at,
        )
        .order_by("-created_at")
        .values_list("composite_score", flat=True)
        .first()
    )
    return float(prev) if prev is not None else None


def _top_tasks(run) -> list[dict]:
    """Highest-priority recommendations, flattened to plain dicts.

    Dicts rather than model instances so `blocks` stays ORM-free.
    """
    from apps.analyzer.services.attribution import attribution_for

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    recs = list(run.recommendations.all()[: TOP_TASKS * 4])
    recs.sort(key=lambda r: order.get((r.priority or "").lower(), 9))
    return [
        {
            "title": r.title,
            "priority": r.priority,
            "signal": attribution_for(r.pillar or "", r.finding_code or "", r.evidence).get(
                "signal", ""
            ),
        }
        for r in recs[:TOP_TASKS]
    ]


def notify_analysis_complete(run) -> bool:
    """Post the report. Returns True when a message was sent.

    Swallows every failure by design — this runs off a post_save signal, and an
    unreachable Slack must not roll back or crash the analysis.
    """
    try:
        integration = _integration_for(run.organization_id)
        if integration is None:
            return False

        channel = (integration.metadata or {}).get("channel_id", "")
        token = integration.get_access_token()
        if not channel or not token:
            logger.warning("slack: integration %s has no channel or token", integration.pk)
            return False

        org = getattr(run, "organization", None)
        brand = (getattr(run, "brand_name", "") or getattr(org, "name", "") or run.url).strip()
        score = float(run.composite_score or 0)
        prev = _previous_score(run)

        payload = slack_blocks.analysis_complete_blocks(
            brand=brand,
            url=run.url,
            score=score,
            delta=None if prev is None else score - prev,
            tasks=_top_tasks(run),
            dashboard_url=_dashboard_url(getattr(org, "slug", "") or ""),
        )
        slack_client.post_message(
            token=token,
            channel=channel,
            blocks=payload,
            fallback=f"GEO analysis complete for {brand} — {score:.0f}/100",
        )
        return True
    except Exception:
        logger.exception("slack: failed to post analysis report for run=%s", getattr(run, "pk", "?"))
        return False
