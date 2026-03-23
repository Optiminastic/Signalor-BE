"""Shopify REST Admin API integration service (OAuth + data sync)."""
import logging
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import hmac
from urllib.parse import parse_qsl, urlencode

import requests

logger = logging.getLogger("apps")

API_VERSION = "2026-01"
AUTH_SCOPES = ["read_products", "read_orders", "read_customers"]


def normalize_shop_domain(shop_domain: str) -> str:
    domain = shop_domain.strip().lower()
    domain = domain.replace("https://", "").replace("http://", "")
    domain = domain.rstrip("/")

    # New Shopify admin URL: admin.shopify.com/store/<handle>[/...]
    if "admin.shopify.com/store/" in domain:
        handle = domain.split("admin.shopify.com/store/")[1].split("/")[0]
        return f"{handle}.myshopify.com"

    # Strip any path (e.g. mystore.myshopify.com/admin/products → mystore.myshopify.com)
    domain = domain.split("/")[0]

    if not domain.endswith(".myshopify.com"):
        # Use only the first subdomain/label as the store handle
        subdomain = domain.split(".")[0]
        return f"{subdomain}.myshopify.com"

    return domain


def build_shopify_oauth_url(
    shop_domain: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: list[str] | None = None,
) -> str:
    domain = normalize_shop_domain(shop_domain)
    scope_str = ",".join(scopes or AUTH_SCOPES)
    query = urlencode(
        {
            "client_id": client_id,
            "scope": scope_str,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"https://{domain}/admin/oauth/authorize?{query}"


def verify_shopify_oauth_hmac(raw_query_string: str, shared_secret: str) -> bool:
    pairs = parse_qsl(raw_query_string, keep_blank_values=True)
    signed = [(k, v) for k, v in pairs if k not in {"hmac", "signature"}]
    given = next((v for k, v in pairs if k == "hmac"), "")
    if not given:
        return False

    message = "&".join(f"{k}={v}" for k, v in sorted(signed, key=lambda x: x[0]))
    digest = hmac.new(
        shared_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(digest, given)


def exchange_shopify_oauth_code(
    shop_domain: str,
    client_id: str,
    client_secret: str,
    code: str,
) -> dict:
    domain = normalize_shop_domain(shop_domain)
    url = f"https://{domain}/admin/oauth/access_token"
    resp = requests.post(
        url,
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise ValueError(f"Failed token exchange (HTTP {resp.status_code}).")
    return resp.json()


def register_app_uninstalled_webhook(
    shop_domain: str,
    access_token: str,
    callback_url: str,
) -> None:
    domain = normalize_shop_domain(shop_domain)
    url = f"https://{domain}/admin/api/{API_VERSION}/webhooks.json"
    headers = {"X-Shopify-Access-Token": access_token}
    payload = {
        "webhook": {
            "topic": "app/uninstalled",
            "address": callback_url,
            "format": "json",
        }
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    if resp.status_code not in (200, 201, 422):
        logger.warning("Failed to register app/uninstalled webhook: HTTP %s", resp.status_code)


def verify_shopify_webhook_hmac(body: bytes, hmac_header: str, shared_secret: str) -> bool:
    import base64

    digest = hmac.new(shared_secret.encode("utf-8"), body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed, hmac_header or "")


def validate_shopify_connection(shop_domain: str, access_token: str) -> dict:
    """Validate the Shopify connection by calling GET /admin/api/.../shop.json.

    Returns shop info dict on success, raises ValueError on failure.
    """
    normalized_domain = normalize_shop_domain(shop_domain)
    url = f"https://{normalized_domain}/admin/api/{API_VERSION}/shop.json"
    headers = {"X-Shopify-Access-Token": access_token}

    try:
        resp = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as e:
        raise ValueError(f"Could not reach Shopify: {e}")

    if resp.status_code == 401:
        raise ValueError("Invalid access token. Check your Custom App credentials.")
    if resp.status_code == 402:
        # Common for dev stores: unpaid Shopify invoice → shop "frozen" → Admin API returns 402.
        raise ValueError(
            "SHOPIFY_SHOP_FROZEN: Shopify returned 402 Payment Required. "
            "Log into that store's admin (Shopify) and clear any outstanding bill, "
            "or use a different development store."
        )
    if resp.status_code == 404:
        raise ValueError("Shop not found. Check the store domain.")
    if resp.status_code != 200:
        raise ValueError(f"Shopify API error (HTTP {resp.status_code}).")

    return resp.json().get("shop", {})


def fetch_shopify_data(integration, days: int = 30) -> dict:
    """Fetch orders from Shopify and compute summary metrics.

    Returns a dict ready to populate a ShopifyDataSnapshot.
    """
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


def create_shopify_blog_article(
    integration,
    title: str,
    content_html: str,
    summary_html: str = "",
    publish: bool = False,
    tags: list[str] | None = None,
) -> dict:
    """
    Create a Shopify blog article.
    Requires write_content scope on the app/token.
    """
    shop_domain = integration.metadata.get("shop_domain", "")
    access_token = integration.get_access_token()
    scope = str(integration.metadata.get("scope", ""))

    if "write_content" not in scope:
        raise ValueError(
            "Shopify token missing write_content scope. Reconnect Shopify app with write_content."
        )

    headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json",
    }

    blogs_url = f"https://{shop_domain}/admin/api/{API_VERSION}/blogs.json?limit=1"
    blogs_resp = requests.get(blogs_url, headers=headers, timeout=20)
    if blogs_resp.status_code != 200:
        raise ValueError(f"Failed to fetch Shopify blogs (HTTP {blogs_resp.status_code}).")

    blogs = blogs_resp.json().get("blogs", [])
    if blogs:
        blog_id = blogs[0].get("id")
    else:
        create_blog_url = f"https://{shop_domain}/admin/api/{API_VERSION}/blogs.json"
        create_blog_payload = {"blog": {"title": "News"}}
        create_blog_resp = requests.post(
            create_blog_url,
            headers=headers,
            json=create_blog_payload,
            timeout=20,
        )
        if create_blog_resp.status_code not in (200, 201):
            raise ValueError(
                f"Failed to create Shopify blog container (HTTP {create_blog_resp.status_code})."
            )
        blog_id = (create_blog_resp.json().get("blog") or {}).get("id")

    if not blog_id:
        raise ValueError("No Shopify blog available to publish article.")

    article_url = f"https://{shop_domain}/admin/api/{API_VERSION}/blogs/{blog_id}/articles.json"
    article_payload = {
        "article": {
            "title": title.strip(),
            "body_html": content_html.strip(),
            "summary_html": summary_html.strip(),
            "published": bool(publish),
            "tags": ", ".join(tags or []),
        }
    }
    article_resp = requests.post(
        article_url,
        headers=headers,
        json=article_payload,
        timeout=25,
    )
    if article_resp.status_code not in (200, 201):
        raise ValueError(
            f"Shopify article create failed (HTTP {article_resp.status_code}): {article_resp.text[:200]}"
        )

    article = article_resp.json().get("article", {})
    return {
        "id": article.get("id"),
        "url": article.get("url"),
        "title": article.get("title"),
        "published": article.get("published"),
    }
