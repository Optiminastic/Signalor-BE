"""
Live GA4 numbers for the dashboard's top-bar indicator.

Split from `ga4.py` because the freshness contract is different: that module
pulls 30-day rollups on a background sync, this one is polled from every open
dashboard and must be cheap, bounded and quick to fail.

Two calls with deliberately different windows:

* `fetch_realtime_snapshot` — the Realtime API, genuinely "right now".
* `fetch_today_sources`     — the ordinary Data API, because **the Realtime API
  has no traffic-source dimension**. Its dimension list is limited to country,
  city, deviceCategory, eventName, minutesAgo, platform, streamId and audiences;
  `sessionSource` simply does not exist there. Source therefore cannot be live,
  and the UI labels this block "today" rather than pretending otherwise.

Both raise on failure. The caller decides how to degrade — this endpoint feeds
the top bar on every page, so it must never turn an outage into a 500.
"""

import logging

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    MetricAggregation,
    MinuteRange,
    OrderBy,
    RunRealtimeReportRequest,
    RunReportRequest,
)

from apps.integrations.models import Integration

logger = logging.getLogger("apps")

# The Realtime API caps `start_minutes_ago` at 29 on standard properties (59 is
# GA360 only). Passing 30 is an error, not a silent clamp.
LIVE_WINDOW_MINUTES = 30
_MAX_MINUTES_AGO = LIVE_WINDOW_MINUTES - 1

# A request that outlives the user's patience is worse than no data: gunicorn
# runs with a 600s timeout, so an unbounded GA call pins a worker for ten
# minutes on an endpoint that every dashboard page polls.
_GA_TIMEOUT_SEC = 6.0

# GA returns the `metric_aggregations` total as an ordinary row whose dimension
# values are this literal. It must be read for the headline and removed from the
# breakdown, or the top country is counted twice.
_TOTAL_ROW = "RESERVED_TOTAL"

_COUNTRY_LIMIT = 8
_SOURCE_LIMIT = 15


class NoPropertySelected(RuntimeError):
    """The integration is connected but no GA4 property was ever chosen."""


def _client(integration: Integration) -> tuple[BetaAnalyticsDataClient, str]:
    """Authorised client plus the `properties/{id}` path, refreshing if needed."""
    property_id = integration.metadata.get("property_id")
    if not property_id:
        raise NoPropertySelected("No GA4 property selected for this integration.")
    # Imported here, not at module scope: a view imports this service, and the
    # credential helpers live in the views package — at module scope that closes
    # an import cycle the moment any view module pulls this one in.
    from apps.integrations.views import _build_credentials, _refresh_if_needed

    creds = _refresh_if_needed(integration, _build_credentials(integration))
    return BetaAnalyticsDataClient(credentials=creds), f"properties/{property_id}"


def _log_quota(response) -> None:
    """Warn as the realtime hourly budget runs down.

    Free signal — `return_property_quota` costs nothing extra — and the hourly
    ceiling is the thing most likely to bite this feature in production.
    """
    quota = getattr(response, "property_quota", None)
    tokens = getattr(quota, "realtime_hourly_tokens", None) if quota else None
    if tokens is not None and getattr(tokens, "remaining", 1) <= 0:
        logger.warning("ga4 realtime: hourly token budget exhausted")


def fetch_realtime_snapshot(integration: Integration) -> dict:
    """Active users in the last 30 minutes, plus a country breakdown."""
    client, property_path = _client(integration)
    request = RunRealtimeReportRequest(
        property=property_path,
        dimensions=[Dimension(name="country"), Dimension(name="countryId")],
        metrics=[Metric(name="activeUsers")],
        minute_ranges=[MinuteRange(start_minutes_ago=_MAX_MINUTES_AGO, end_minutes_ago=0)],
        # `activeUsers` is a de-duplicated user count, so summing the country
        # rows overstates the headline. Only the aggregate row is correct.
        metric_aggregations=[MetricAggregation.TOTAL],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="activeUsers"), desc=True)],
        limit=_COUNTRY_LIMIT,
        return_property_quota=True,
    )
    response = client.run_realtime_report(request, timeout=_GA_TIMEOUT_SEC)
    _log_quota(response)
    return _parse_realtime(response)


def _parse_realtime(response) -> dict:
    """Split GA's rows into the headline total and the country breakdown."""
    total = 0
    countries: list[dict] = []
    for row in response.rows:
        name = row.dimension_values[0].value or ""
        code = row.dimension_values[1].value or ""
        users = int(row.metric_values[0].value or 0)
        if name == _TOTAL_ROW or code == _TOTAL_ROW:
            total = users
            continue
        countries.append({"code": code, "name": name, "users": users})

    # Older responses can omit the aggregate row entirely; a summed fallback
    # over-counts a little, which beats reporting nobody at all.
    if not total and countries:
        total = sum(c["users"] for c in countries)
    return {"active_users": total, "countries": countries}


def fetch_today_sources(integration: Integration) -> list[dict]:
    """Today's sessions by source — the closest thing to "where did they come
    from" that GA exposes, since Realtime has no source dimension."""
    client, property_path = _client(integration)
    request = RunReportRequest(
        property=property_path,
        date_ranges=[DateRange(start_date="today", end_date="today")],
        dimensions=[
            Dimension(name="sessionSource"),
            Dimension(name="sessionDefaultChannelGroup"),
        ],
        metrics=[Metric(name="sessions")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
        limit=_SOURCE_LIMIT,
    )
    response = client.run_report(request, timeout=_GA_TIMEOUT_SEC)
    # Returned raw. Classifying a host as "ChatGPT" is presentation, and the
    # engine/label/logo maps all live on the frontend — duplicating them here
    # would be a second source of truth that silently drifts.
    return [
        {
            "source": row.dimension_values[0].value or "",
            "channel": row.dimension_values[1].value or "",
            "sessions": int(row.metric_values[0].value or 0),
        }
        for row in response.rows
    ]
