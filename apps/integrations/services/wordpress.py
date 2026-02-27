"""
WordPress REST API integration service.
Uses WordPress username + Application Password (Basic Auth).
"""
from __future__ import annotations

import base64
import logging
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

import requests

logger = logging.getLogger("apps")


def _auth_header(username: str, app_password: str) -> dict[str, str]:
    raw = f"{username}:{app_password}".encode("utf-8")
    token = base64.b64encode(raw).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _normalize_site_url(site_url: str) -> str:
    url = site_url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def _parse_wp_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        # WP often returns "YYYY-MM-DDTHH:MM:SS" without timezone
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def validate_wordpress_connection(site_url: str, username: str, app_password: str) -> dict:
    """Validate connectivity and credentials.

    Returns normalized site metadata on success. Raises ValueError on failure.
    """
    normalized_url = _normalize_site_url(site_url)
    headers = _auth_header(username, app_password)

    # Public site info
    root_url = urljoin(normalized_url + "/", "wp-json/")
    try:
        root_resp = requests.get(root_url, timeout=15)
    except requests.RequestException as exc:
        raise ValueError(f"Could not reach WordPress site: {exc}") from exc

    if root_resp.status_code != 200:
        raise ValueError(f"WordPress site returned HTTP {root_resp.status_code}.")

    root_json = root_resp.json()
    site_name = root_json.get("name") or normalized_url

    # Auth check against /users/me
    me_url = urljoin(normalized_url + "/", "wp-json/wp/v2/users/me")
    try:
        me_resp = requests.get(me_url, headers=headers, timeout=15)
    except requests.RequestException as exc:
        raise ValueError(f"WordPress auth check failed: {exc}") from exc

    if me_resp.status_code in (401, 403):
        raise ValueError("Invalid WordPress username or Application Password.")
    if me_resp.status_code != 200:
        raise ValueError(f"WordPress auth API error (HTTP {me_resp.status_code}).")

    me_json = me_resp.json()
    return {
        "site_url": normalized_url,
        "site_name": site_name,
        "wp_version": root_json.get("generator", ""),
        "user_id": me_json.get("id"),
        "user_name": me_json.get("name") or username,
    }


def fetch_wordpress_data(integration, days: int = 30) -> dict:
    """Fetch WordPress content metrics and trend data for snapshot storage."""
    site_url = _normalize_site_url(integration.metadata.get("site_url", ""))
    username = integration.metadata.get("username", "")
    app_password = integration.get_access_token()

    headers = _auth_header(username, app_password)
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    total_posts = _get_collection_total(site_url, "posts", headers)
    total_pages = _get_collection_total(site_url, "pages", headers)

    recent_by_date = _fetch_recent_posts(site_url, headers, "date")
    recent_by_modified = _fetch_recent_posts(site_url, headers, "modified")

    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    published_posts_30d = sum(
        1 for post in recent_by_date if (_parse_wp_datetime(post.get("date_gmt") or post.get("date")) or datetime.min.replace(tzinfo=timezone.utc)) >= start_dt
    )
    updated_posts_30d = sum(
        1 for post in recent_by_modified if (_parse_wp_datetime(post.get("modified_gmt") or post.get("modified")) or datetime.min.replace(tzinfo=timezone.utc)) >= start_dt
    )

    top_posts = []
    for post in recent_by_date[:10]:
        title = (post.get("title") or {}).get("rendered", "") if isinstance(post.get("title"), dict) else ""
        top_posts.append(
            {
                "id": post.get("id"),
                "title": title,
                "slug": post.get("slug", ""),
                "url": post.get("link", ""),
                "published_at": post.get("date"),
                "modified_at": post.get("modified"),
            }
        )

    daily_publishing = _compute_daily_publishing(recent_by_date, start_date, end_date)

    return {
        "date_start": start_date,
        "date_end": end_date,
        "total_posts": total_posts,
        "total_pages": total_pages,
        "published_posts_30d": published_posts_30d,
        "updated_posts_30d": updated_posts_30d,
        "top_posts": top_posts,
        "daily_publishing": daily_publishing,
    }


def _get_collection_total(site_url: str, collection: str, headers: dict[str, str]) -> int:
    url = urljoin(site_url + "/", f"wp-json/wp/v2/{collection}")
    resp = requests.get(
        url,
        headers=headers,
        params={"per_page": 1, "status": "publish"},
        timeout=20,
    )
    if resp.status_code != 200:
        logger.warning("Failed to read %s total from WordPress: HTTP %s", collection, resp.status_code)
        return 0
    return int(resp.headers.get("X-WP-Total", "0"))


def _fetch_recent_posts(site_url: str, headers: dict[str, str], orderby: str) -> list[dict]:
    """Fetch recent published posts ordered by a field until entries are old."""
    url = urljoin(site_url + "/", "wp-json/wp/v2/posts")
    results: list[dict] = []
    page = 1
    per_page = 100

    while page <= 10:  # cap requests for safety
        resp = requests.get(
            url,
            headers=headers,
            params={
                "status": "publish",
                "orderby": orderby,
                "order": "desc",
                "per_page": per_page,
                "page": page,
                "_fields": "id,date,date_gmt,modified,modified_gmt,slug,link,title",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning("Failed to fetch WordPress posts page %s: HTTP %s", page, resp.status_code)
            break

        page_items = resp.json()
        if not page_items:
            break
        results.extend(page_items)

        total_pages = int(resp.headers.get("X-WP-TotalPages", "1"))
        if page >= total_pages:
            break
        page += 1

    return results


def _compute_daily_publishing(posts: list[dict], start_date: date, end_date: date) -> list[dict]:
    daily = {}
    current = start_date
    while current <= end_date:
        key = current.isoformat()
        daily[key] = {"date": key, "published_posts": 0}
        current += timedelta(days=1)

    for post in posts:
        dt = _parse_wp_datetime(post.get("date_gmt") or post.get("date"))
        if not dt:
            continue
        key = dt.date().isoformat()
        if key in daily:
            daily[key]["published_posts"] += 1

    return sorted(daily.values(), key=lambda row: row["date"])


def publish_wordpress_post(
    integration,
    title: str,
    content: str,
    excerpt: str = "",
    status: str = "draft",
    slug: str = "",
) -> dict:
    """
    Publish or save a draft post to WordPress.
    Returns a compact post payload for UI confirmation.
    """
    site_url = _normalize_site_url(integration.metadata.get("site_url", ""))
    username = integration.metadata.get("username", "")
    app_password = integration.get_access_token()
    headers = _auth_header(username, app_password)
    headers["Content-Type"] = "application/json"

    post_url = urljoin(site_url + "/", "wp-json/wp/v2/posts")
    wp_status = "publish" if status == "publish" else "draft"
    payload = {
        "title": title.strip(),
        "content": content.strip(),
        "excerpt": excerpt.strip(),
        "status": wp_status,
    }
    if slug.strip():
        payload["slug"] = slug.strip()

    try:
        resp = requests.post(post_url, headers=headers, json=payload, timeout=25)
    except requests.RequestException as exc:
        raise ValueError(f"Failed to reach WordPress publish API: {exc}") from exc

    if resp.status_code not in (200, 201):
        raise ValueError(
            f"WordPress publish failed (HTTP {resp.status_code}): {resp.text[:200]}"
        )

    data = resp.json()
    return {
        "id": data.get("id"),
        "url": data.get("link"),
        "status": data.get("status"),
        "title": (data.get("title") or {}).get("rendered", title),
    }
