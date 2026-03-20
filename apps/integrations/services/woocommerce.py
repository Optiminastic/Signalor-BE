"""
WooCommerce REST API integration service.

Auth: HTTP Basic Auth using Consumer Key (username) + Consumer Secret (password).
Base: {site_url}/wp-json/wc/v3/
Docs: https://woocommerce.github.io/woocommerce-rest-api-docs/
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urljoin

import requests

logger = logging.getLogger("apps")

WC_API_VERSION = "wc/v3"


def _normalize_site_url(site_url: str) -> str:
    url = site_url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def _wc_url(site_url: str, path: str) -> str:
    base = _normalize_site_url(site_url)
    return urljoin(base + "/", f"wp-json/{WC_API_VERSION}/{path.lstrip('/')}")


def _auth(consumer_key: str, consumer_secret: str) -> tuple[str, str]:
    return (consumer_key, consumer_secret)


def validate_woocommerce_connection(
    site_url: str, consumer_key: str, consumer_secret: str
) -> dict:
    """
    Validate WooCommerce credentials by calling GET /wp-json/wc/v3/system_status.
    Returns basic site info dict on success. Raises ValueError on failure.
    """
    normalized = _normalize_site_url(site_url)
    url = _wc_url(normalized, "system_status")
    try:
        resp = requests.get(
            url,
            auth=_auth(consumer_key, consumer_secret),
            timeout=15,
        )
    except requests.RequestException as exc:
        raise ValueError(f"Could not reach WooCommerce site: {exc}") from exc

    if resp.status_code in (401, 403):
        raise ValueError("Invalid Consumer Key or Consumer Secret.")
    if resp.status_code == 404:
        raise ValueError(
            "WooCommerce REST API not found. Make sure WooCommerce is installed and the REST API is enabled."
        )
    if resp.status_code != 200:
        raise ValueError(f"WooCommerce API error (HTTP {resp.status_code}).")

    data = resp.json()
    environment = data.get("environment", {})
    return {
        "site_url": normalized,
        "site_name": environment.get("site_title", normalized.split("//")[-1]),
        "wc_version": environment.get("wc_version", ""),
        "wp_version": environment.get("wp_version", ""),
    }


def fetch_woocommerce_data(integration, days: int = 30) -> dict:
    """
    Fetch WooCommerce orders and products, compute summary metrics.
    Returns a dict ready to populate WooCommerceDataSnapshot.
    """
    site_url = integration.metadata.get("site_url", "")
    consumer_key = integration.metadata.get("consumer_key", "")
    consumer_secret = integration.get_access_token()

    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    orders = _fetch_all_orders(site_url, consumer_key, consumer_secret, start_date, end_date)
    top_products = _fetch_top_products(site_url, consumer_key, consumer_secret)
    daily_orders = _compute_daily_orders(orders, start_date, end_date)

    total_revenue = sum(
        Decimal(str(o.get("total", "0"))) for o in orders
    )
    total_orders = len(orders)
    aov = (total_revenue / total_orders).quantize(Decimal("0.01")) if total_orders else Decimal("0")

    # Unique customers (by billing email)
    customer_emails = {
        (o.get("billing") or {}).get("email", "").lower()
        for o in orders
        if (o.get("billing") or {}).get("email")
    }
    total_customers = len(customer_emails)

    # Total products (separate quick call)
    total_products = _get_total_products(site_url, consumer_key, consumer_secret)

    return {
        "date_start": start_date,
        "date_end": end_date,
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "average_order_value": aov,
        "total_products": total_products,
        "total_customers": total_customers,
        "top_products": top_products,
        "daily_orders": daily_orders,
    }


def _fetch_all_orders(
    site_url: str,
    consumer_key: str,
    consumer_secret: str,
    start_date: date,
    end_date: date,
) -> list[dict]:
    auth = _auth(consumer_key, consumer_secret)
    url = _wc_url(site_url, "orders")
    results: list[dict] = []
    page = 1
    per_page = 100

    after = start_date.isoformat() + "T00:00:00"
    before = end_date.isoformat() + "T23:59:59"

    while page <= 20:
        try:
            resp = requests.get(
                url,
                auth=auth,
                params={
                    "per_page": per_page,
                    "page": page,
                    "after": after,
                    "before": before,
                    "status": "completed,processing",
                    "_fields": "id,total,date_created,billing,line_items",
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            logger.warning("WooCommerce orders fetch error (page %d): %s", page, exc)
            break

        if resp.status_code != 200:
            logger.warning("WooCommerce orders fetch failed: HTTP %s", resp.status_code)
            break

        page_data = resp.json()
        if not page_data:
            break

        results.extend(page_data)
        total_pages = int(resp.headers.get("X-WP-TotalPages", "1"))
        if page >= total_pages:
            break
        page += 1

    return results


def _fetch_top_products(
    site_url: str, consumer_key: str, consumer_secret: str, limit: int = 10
) -> list[dict]:
    auth = _auth(consumer_key, consumer_secret)
    url = _wc_url(site_url, "products")
    try:
        resp = requests.get(
            url,
            auth=auth,
            params={
                "per_page": limit,
                "orderby": "popularity",
                "order": "desc",
                "status": "publish",
                "_fields": "id,name,slug,permalink,price,total_sales,stock_status",
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        logger.warning("WooCommerce products fetch error: %s", exc)
        return []

    if resp.status_code != 200:
        return []

    products = []
    for p in resp.json():
        products.append({
            "id": p.get("id"),
            "name": p.get("name", ""),
            "slug": p.get("slug", ""),
            "permalink": p.get("permalink", ""),
            "price": p.get("price", "0"),
            "total_sales": p.get("total_sales", 0),
            "stock_status": p.get("stock_status", ""),
        })
    return products


def _get_total_products(site_url: str, consumer_key: str, consumer_secret: str) -> int:
    auth = _auth(consumer_key, consumer_secret)
    url = _wc_url(site_url, "products")
    try:
        resp = requests.get(
            url,
            auth=auth,
            params={"per_page": 1, "status": "publish"},
            timeout=10,
        )
        if resp.status_code == 200:
            return int(resp.headers.get("X-WP-Total", "0"))
    except Exception as exc:
        logger.warning("WooCommerce total products fetch error: %s", exc)
    return 0


def _compute_daily_orders(
    orders: list[dict], start_date: date, end_date: date
) -> list[dict]:
    daily: dict[str, dict] = {}
    current = start_date
    while current <= end_date:
        key = current.isoformat()
        daily[key] = {"date": key, "orders": 0, "revenue": "0.00"}
        current += timedelta(days=1)

    for order in orders:
        raw_date = order.get("date_created", "")
        if not raw_date:
            continue
        key = raw_date[:10]
        if key in daily:
            daily[key]["orders"] += 1
            prev = Decimal(str(daily[key]["revenue"]))
            revenue = Decimal(str(order.get("total", "0")))
            daily[key]["revenue"] = str((prev + revenue).quantize(Decimal("0.01")))

    return sorted(daily.values(), key=lambda r: r["date"])


def update_woocommerce_product(
    integration,
    product_id: int,
    updates: dict,
) -> dict:
    """
    Update a WooCommerce product (description, short_description, name, etc.).
    `updates` is a dict of WooCommerce product fields.
    Returns the updated product dict.
    """
    site_url = integration.metadata.get("site_url", "")
    consumer_key = integration.metadata.get("consumer_key", "")
    consumer_secret = integration.get_access_token()
    auth = _auth(consumer_key, consumer_secret)

    url = _wc_url(site_url, f"products/{product_id}")
    try:
        resp = requests.put(url, auth=auth, json=updates, timeout=20)
    except requests.RequestException as exc:
        raise ValueError(f"Failed to reach WooCommerce API: {exc}") from exc

    if resp.status_code not in (200, 201):
        raise ValueError(
            f"WooCommerce product update failed (HTTP {resp.status_code}): {resp.text[:200]}"
        )
    return resp.json()
