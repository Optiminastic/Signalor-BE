"""
Google Analytics 4 data fetching service.

Uses the GA4 Data API v1beta (BetaAnalyticsDataClient) to pull metrics.
"""
import logging
from datetime import date, timedelta

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)

from apps.integrations.models import Integration
from apps.integrations.views import _build_credentials, _refresh_if_needed

logger = logging.getLogger("apps")


def fetch_ga4_data(integration: Integration, days: int = 30) -> dict:
    """
    Fetch GA4 data for the selected property.

    Returns a dict with:
        sessions, organic_sessions, bounce_rate, avg_session_duration,
        top_pages, traffic_sources, daily_trend
    """
    property_id = integration.metadata.get("property_id")
    if not property_id:
        raise ValueError("No GA4 property selected for this integration.")

    creds = _build_credentials(integration)
    creds = _refresh_if_needed(integration, creds)

    client = BetaAnalyticsDataClient(credentials=creds)
    property_path = f"properties/{property_id}"

    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    date_range = DateRange(
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )

    # 1. Summary metrics
    summary = _fetch_summary(client, property_path, date_range)

    # 2. Top pages
    top_pages = _fetch_top_pages(client, property_path, date_range)

    # 3. Traffic sources
    traffic_sources = _fetch_traffic_sources(client, property_path, date_range)

    # 4. Daily trend
    daily_trend = _fetch_daily_trend(client, property_path, date_range)

    return {
        "date_start": start_date.isoformat(),
        "date_end": end_date.isoformat(),
        **summary,
        "top_pages": top_pages,
        "traffic_sources": traffic_sources,
        "daily_trend": daily_trend,
    }


def _fetch_summary(client, property_path, date_range) -> dict:
    """Fetch aggregate metrics: sessions, organic sessions, bounce rate, avg duration."""
    # Total sessions and bounce rate
    response = client.run_report(RunReportRequest(
        property=property_path,
        date_ranges=[date_range],
        metrics=[
            Metric(name="sessions"),
            Metric(name="bounceRate"),
            Metric(name="averageSessionDuration"),
        ],
    ))

    sessions = 0
    bounce_rate = 0.0
    avg_duration = 0.0

    if response.rows:
        row = response.rows[0]
        sessions = int(row.metric_values[0].value or 0)
        bounce_rate = float(row.metric_values[1].value or 0)
        avg_duration = float(row.metric_values[2].value or 0)

    # Organic sessions (filter by sessionDefaultChannelGroup = "Organic Search")
    organic_response = client.run_report(RunReportRequest(
        property=property_path,
        date_ranges=[date_range],
        dimensions=[Dimension(name="sessionDefaultChannelGroup")],
        metrics=[Metric(name="sessions")],
    ))

    organic_sessions = 0
    for row in organic_response.rows:
        channel = row.dimension_values[0].value
        if channel.lower() in ("organic search", "organic"):
            organic_sessions += int(row.metric_values[0].value or 0)

    return {
        "sessions": sessions,
        "organic_sessions": organic_sessions,
        "bounce_rate": round(bounce_rate, 4),
        "avg_session_duration": round(avg_duration, 2),
    }


def _fetch_top_pages(client, property_path, date_range, limit=20) -> list:
    """Fetch top pages by sessions."""
    response = client.run_report(RunReportRequest(
        property=property_path,
        date_ranges=[date_range],
        dimensions=[Dimension(name="pagePath")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="bounceRate"),
            Metric(name="averageSessionDuration"),
        ],
        limit=limit,
    ))

    pages = []
    for row in response.rows:
        pages.append({
            "path": row.dimension_values[0].value,
            "sessions": int(row.metric_values[0].value or 0),
            "bounce_rate": round(float(row.metric_values[1].value or 0), 4),
            "avg_duration": round(float(row.metric_values[2].value or 0), 2),
        })

    return pages


def _fetch_traffic_sources(client, property_path, date_range) -> list:
    """Fetch traffic sources breakdown."""
    response = client.run_report(RunReportRequest(
        property=property_path,
        date_ranges=[date_range],
        dimensions=[
            Dimension(name="sessionSource"),
            Dimension(name="sessionMedium"),
        ],
        metrics=[Metric(name="sessions")],
        limit=50,
    ))

    sources = []
    for row in response.rows:
        sources.append({
            "source": row.dimension_values[0].value,
            "medium": row.dimension_values[1].value,
            "sessions": int(row.metric_values[0].value or 0),
        })

    return sources


def _fetch_daily_trend(client, property_path, date_range) -> list:
    """Fetch daily sessions and organic sessions trend."""
    response = client.run_report(RunReportRequest(
        property=property_path,
        date_ranges=[date_range],
        dimensions=[
            Dimension(name="date"),
            Dimension(name="sessionDefaultChannelGroup"),
        ],
        metrics=[Metric(name="sessions")],
        limit=10000,
    ))

    # Aggregate by date
    daily = {}
    for row in response.rows:
        dt = row.dimension_values[0].value  # YYYYMMDD
        channel = row.dimension_values[1].value
        count = int(row.metric_values[0].value or 0)

        formatted_date = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}"
        if formatted_date not in daily:
            daily[formatted_date] = {"date": formatted_date, "sessions": 0, "organic_sessions": 0}

        daily[formatted_date]["sessions"] += count
        if channel.lower() in ("organic search", "organic"):
            daily[formatted_date]["organic_sessions"] += count

    return sorted(daily.values(), key=lambda x: x["date"])
