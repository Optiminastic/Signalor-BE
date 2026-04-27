"""
Google Analytics 4 data fetching service.

Uses the GA4 Data API v1beta (BetaAnalyticsDataClient) to pull metrics.
"""
import logging
from datetime import date, timedelta
from urllib.parse import urlparse

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Filter,
    FilterExpression,
    FilterExpressionList,
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

    # 5. Country breakdown
    countries = _fetch_countries(client, property_path, date_range)

    return {
        "date_start": start_date.isoformat(),
        "date_end": end_date.isoformat(),
        **summary,
        "top_pages": top_pages,
        "traffic_sources": traffic_sources,
        "daily_trend": daily_trend,
        "countries": countries,
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


def _fetch_countries(client, property_path, date_range, limit=50) -> list:
    """Fetch top countries by sessions — returns ISO alpha-2 country codes + session counts."""
    from google.analytics.data_v1beta.types import OrderBy
    response = client.run_report(RunReportRequest(
        property=property_path,
        date_ranges=[date_range],
        dimensions=[
            Dimension(name="country"),
            Dimension(name="countryId"),   # ISO alpha-2 e.g. "IN", "US"
        ],
        metrics=[Metric(name="sessions")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
        limit=limit,
    ))
    countries = []
    for row in response.rows:
        country_name = row.dimension_values[0].value
        country_id   = row.dimension_values[1].value   # alpha-2
        sessions     = int(row.metric_values[0].value or 0)
        if country_id and country_id != "(not set)":
            countries.append({
                "country": country_name,
                "country_id": country_id.upper(),
                "sessions": sessions,
            })
    return countries


def fetch_ga4_page_metrics(integration: Integration, page_url: str, days: int = 30) -> dict:
    """
    Fetch GA4 metrics for a specific analyzed page path.
    Returns a best-effort page match payload with sessions/bounce/avg duration.
    """
    if not page_url:
        return {"found": False, "page_path": "", "sessions": 0, "bounce_rate": 0.0, "avg_session_duration": 0.0}

    property_id = integration.metadata.get("property_id")
    if not property_id:
        raise ValueError("No GA4 property selected for this integration.")

    parsed = urlparse(page_url)
    page_path = parsed.path or "/"
    analyzed_host = (parsed.hostname or "").lower()

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

    def _run(path_value: str, host_value: str):
        response = client.run_report(
            RunReportRequest(
                property=property_path,
                date_ranges=[date_range],
                dimensions=[Dimension(name="hostName"), Dimension(name="pagePath")],
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="bounceRate"),
                    Metric(name="averageSessionDuration"),
                ],
                dimension_filter=FilterExpression(
                    and_group=FilterExpressionList(
                        expressions=[
                            FilterExpression(
                                filter=Filter(
                                    field_name="hostName",
                                    string_filter=Filter.StringFilter(
                                        value=host_value,
                                        match_type=Filter.StringFilter.MatchType.EXACT,
                                    ),
                                )
                            ),
                            FilterExpression(
                                filter=Filter(
                                    field_name="pagePath",
                                    string_filter=Filter.StringFilter(
                                        value=path_value,
                                        match_type=Filter.StringFilter.MatchType.EXACT,
                                    ),
                                )
                            ),
                        ]
                    )
                ),
                limit=1,
            )
        )
        if not response.rows:
            return None
        row = response.rows[0]
        return {
            "found": True,
            "host_match": True,
            "analyzed_host": host_value,
            "matched_host": row.dimension_values[0].value,
            "page_path": row.dimension_values[1].value,
            "sessions": int(row.metric_values[0].value or 0),
            "bounce_rate": round(float(row.metric_values[1].value or 0), 4),
            "avg_session_duration": round(float(row.metric_values[2].value or 0), 2),
        }

    host_variants = [analyzed_host] if analyzed_host else []
    if analyzed_host.startswith("www."):
        host_variants.append(analyzed_host.removeprefix("www."))
    elif analyzed_host:
        host_variants.append(f"www.{analyzed_host}")

    path_variants = [page_path]
    alt_path = page_path[:-1] if page_path.endswith("/") and page_path != "/" else f"{page_path}/"
    if alt_path != page_path:
        path_variants.append(alt_path)

    match = None
    for hv in host_variants:
        for pv in path_variants:
            match = _run(pv, hv)
            if match:
                return match

    # If host+path exact doesn't match, check if analyzed host exists in property at all.
    host_seen = False
    for hv in host_variants:
        host_response = client.run_report(
            RunReportRequest(
                property=property_path,
                date_ranges=[date_range],
                dimensions=[Dimension(name="hostName")],
                metrics=[Metric(name="sessions")],
                dimension_filter=FilterExpression(
                    filter=Filter(
                        field_name="hostName",
                        string_filter=Filter.StringFilter(
                            value=hv,
                            match_type=Filter.StringFilter.MatchType.EXACT,
                        ),
                    )
                ),
                limit=1,
            )
        )
        if host_response.rows:
            host_seen = True
            break

    return {
        "found": False,
        "host_match": host_seen,
        "analyzed_host": analyzed_host,
        "matched_host": "",
        "page_path": page_path,
        "sessions": 0,
        "bounce_rate": 0.0,
        "avg_session_duration": 0.0,
    }
