"""
Shopify REST Admin API integration service.
Uses Custom App access tokens (no OAuth flow needed).
"""
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

import requests

logger = logging.getLogger("apps")

API_VERSION = "2024-01"


def validate_shopify_connection(shop_domain: str, access_token: str) -> dict:
    """Validate the Shopify connection by calling GET /admin/api/.../shop.json.

    Returns shop info dict on success, raises ValueError on failure.
    """
    url = f"https://{shop_domain}/admin/api/{API_VERSION}/shop.json"
    headers = {"X-Shopify-Access-Token": access_token}

    try:
        resp = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as e:
        raise ValueError(f"Could not reach Shopify: {e}")

    if resp.status_code == 401:
        raise ValueError("Invalid access token. Check your Custom App credentials.")
    if resp.status_code == 404:
        raise ValueError("Shop not found. Check the store domain.")
    if resp.status_code != 200:
        raise ValueError(f"Shopify API error (HTTP {resp.status_code}).")

    return resp.json().get("shop", {})


def fetch_shopify_data(integration, days: int = 30) -> dict:
    """Fetch orders from Shopify and compute summary metrics.

    Returns a dict ready to populate a ShopifyDataSnapshot.
    """
    from ..models import decrypt_token

    shop_domain = integration.metadata.get("shop_domain", "")
    access_token = integration.get_access_token()

    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    orders = _fetch_all_orders(shop_domain, access_token, start_date, end_date)

    total_orders = len(orders)
    total_revenue = Decimal("0")
    customer_ids = set()

    for order in orders:
        total_revenue += Decimal(str(order.get("total_price", "0")))
        customer = order.get("customer")
        if customer and customer.get("id"):
            customer_ids.add(customer["id"])

    average_order_value = (
        (total_revenue / total_orders) if total_orders > 0 else Decimal("0")
    )

    top_products = _compute_top_products(orders)
    daily_orders = _compute_daily_trends(orders, start_date, end_date)

    return {
        "date_start": start_date,
        "date_end": end_date,
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "average_order_value": average_order_value,
        "total_customers": len(customer_ids),
        "top_products": top_products,
        "daily_orders": daily_orders,
    }


def _fetch_all_orders(
    shop_domain: str, access_token: str, start_date: date, end_date: date
) -> list:
    """Fetch all orders in the date range, handling pagination via Link header."""
    headers = {"X-Shopify-Access-Token": access_token}
    params = {
        "status": "any",
        "created_at_min": f"{start_date}T00:00:00Z",
        "created_at_max": f"{end_date}T23:59:59Z",
        "limit": 250,
        "fields": "id,total_price,created_at,line_items,customer",
    }

    url = f"https://{shop_domain}/admin/api/{API_VERSION}/orders.json"
    all_orders = []

    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            logger.error("Shopify orders fetch failed: HTTP %s", resp.status_code)
            break

        data = resp.json()
        all_orders.extend(data.get("orders", []))

        # Pagination via Link header
        url = None
        params = None  # params only for first request
        link_header = resp.headers.get("Link", "")
        if 'rel="next"' in link_header:
            for part in link_header.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
                    break

    return all_orders


def _compute_top_products(orders: list, limit: int = 10) -> list:
    """Aggregate line_items by product, return top N by revenue."""
    product_map: dict[str, dict] = {}

    for order in orders:
        for item in order.get("line_items", []):
            pid = str(item.get("product_id", "unknown"))
            title = item.get("title", "Unknown Product")
            qty = item.get("quantity", 0)
            price = Decimal(str(item.get("price", "0"))) * qty

            if pid not in product_map:
                product_map[pid] = {
                    "product_id": pid,
                    "title": title,
                    "quantity_sold": 0,
                    "revenue": Decimal("0"),
                }
            product_map[pid]["quantity_sold"] += qty
            product_map[pid]["revenue"] += price

    products = sorted(
        product_map.values(), key=lambda p: p["revenue"], reverse=True
    )[:limit]

    # Convert Decimal to string for JSON serialization
    for p in products:
        p["revenue"] = str(p["revenue"])

    return products


def _compute_daily_trends(orders: list, start_date: date, end_date: date) -> list:
    """Compute daily order count + revenue, filling zero-days."""
    daily: dict[str, dict] = {}

    # Initialize all days
    current = start_date
    while current <= end_date:
        key = current.isoformat()
        daily[key] = {"date": key, "orders": 0, "revenue": Decimal("0")}
        current += timedelta(days=1)

    # Aggregate orders
    for order in orders:
        created = order.get("created_at", "")[:10]  # "YYYY-MM-DD"
        if created in daily:
            daily[created]["orders"] += 1
            daily[created]["revenue"] += Decimal(str(order.get("total_price", "0")))

    # Sort by date and convert Decimal
    result = sorted(daily.values(), key=lambda d: d["date"])
    for d in result:
        d["revenue"] = str(d["revenue"])

    return result
