"""
Background tasks for integration data syncing.
Follows the same threading.Thread pattern as apps/analyzer/tasks.py.
"""
import logging
import threading
from datetime import date, timedelta

from .models import GADataSnapshot, Integration, ShopifyDataSnapshot

logger = logging.getLogger("apps")


def start_ga4_sync(integration_id: int):
    """Spawn a daemon thread to sync GA4 data."""
    thread = threading.Thread(
        target=_run_ga4_sync,
        args=(integration_id,),
        daemon=True,
    )
    thread.start()
    return thread


def _run_ga4_sync(integration_id: int):
    """Fetch GA4 data and store as a snapshot."""
    try:
        integration = Integration.objects.get(pk=integration_id)
    except Integration.DoesNotExist:
        logger.error("Integration %s not found for sync", integration_id)
        return

    # Create a pending snapshot
    end_date = date.today()
    start_date = end_date - timedelta(days=30)

    snapshot = GADataSnapshot.objects.create(
        integration=integration,
        date_start=start_date,
        date_end=end_date,
        sync_status="syncing",
    )

    try:
        from .services.ga4 import fetch_ga4_data

        data = fetch_ga4_data(integration, days=30)

        snapshot.sessions = data["sessions"]
        snapshot.organic_sessions = data["organic_sessions"]
        snapshot.bounce_rate = data["bounce_rate"]
        snapshot.avg_session_duration = data["avg_session_duration"]
        snapshot.top_pages = data["top_pages"]
        snapshot.traffic_sources = data["traffic_sources"]
        snapshot.daily_trend = data["daily_trend"]
        snapshot.sync_status = "complete"
        snapshot.save()

        logger.info(
            "GA4 sync complete for integration %s: %d sessions",
            integration_id, data["sessions"],
        )

    except Exception as e:
        logger.error("GA4 sync failed for integration %s: %s", integration_id, str(e))
        snapshot.sync_status = "failed"
        snapshot.error_message = str(e)
        snapshot.save(update_fields=["sync_status", "error_message"])


def start_shopify_sync(integration_id: int):
    """Spawn a daemon thread to sync Shopify data."""
    thread = threading.Thread(
        target=_run_shopify_sync,
        args=(integration_id,),
        daemon=True,
    )
    thread.start()
    return thread


def _run_shopify_sync(integration_id: int):
    """Fetch Shopify data and store as a snapshot."""
    try:
        integration = Integration.objects.get(pk=integration_id)
    except Integration.DoesNotExist:
        logger.error("Integration %s not found for Shopify sync", integration_id)
        return

    end_date = date.today()
    start_date = end_date - timedelta(days=30)

    snapshot = ShopifyDataSnapshot.objects.create(
        integration=integration,
        date_start=start_date,
        date_end=end_date,
        sync_status="syncing",
    )

    try:
        from .services.shopify import fetch_shopify_data

        data = fetch_shopify_data(integration, days=30)

        snapshot.total_orders = data["total_orders"]
        snapshot.total_revenue = data["total_revenue"]
        snapshot.average_order_value = data["average_order_value"]
        snapshot.total_customers = data["total_customers"]
        snapshot.top_products = data["top_products"]
        snapshot.daily_orders = data["daily_orders"]
        snapshot.sync_status = "complete"
        snapshot.save()

        logger.info(
            "Shopify sync complete for integration %s: %d orders",
            integration_id, data["total_orders"],
        )

    except Exception as e:
        logger.error("Shopify sync failed for integration %s: %s", integration_id, str(e))
        snapshot.sync_status = "failed"
        snapshot.error_message = str(e)
        snapshot.save(update_fields=["sync_status", "error_message"])
