"""
WordPress REST API integration service.
Supports both self-hosted WordPress (Basic Auth) and WordPress.com (OAuth2 via public API).
"""

from __future__ import annotations

import base64
import logging
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urljoin

import requests

logger = logging.getLogger("apps")


def _auth_header(username: str, app_password: str) -> dict[str, str]:
    raw = f"{username}:{app_password}".encode()
    token = base64.b64encode(raw).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _wpcom_auth_header(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _is_wpcom(integration) -> bool:
    return bool(integration.metadata.get("is_wpcom", False))


def _normalize_site_url(site_url: str) -> str:
    url = site_url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def _parse_wp_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# WordPress.com OAuth2 + public API
# ---------------------------------------------------------------------------

WPCOM_TOKEN_URL = "https://public-api.wordpress.com/oauth2/token"
WPCOM_API_BASE = "https://public-api.wordpress.com/rest/v1.1"


def exchange_wpcom_oauth_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> dict:
    """Exchange WordPress.com OAuth2 code for access token."""
    resp = requests.post(
        WPCOM_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise ValueError(f"WordPress.com token exchange failed (HTTP {resp.status_code}): {resp.text[:300]}")
    return resp.json()


def validate_wpcom_token(access_token: str, blog_id: str) -> dict:
    """Validate WordPress.com access token and get site info."""
    headers = _wpcom_auth_header(access_token)

    # Get site info
    site_url = f"{WPCOM_API_BASE}/sites/{blog_id}"
    resp = requests.get(site_url, headers=headers, timeout=15)

    if resp.status_code != 200:
        raise ValueError(f"WordPress.com site validation failed (HTTP {resp.status_code})")

    site_data = resp.json()

    # Get user info
    me_url = f"{WPCOM_API_BASE}/me"
    me_resp = requests.get(me_url, headers=headers, timeout=15)
    me_data = me_resp.json() if me_resp.status_code == 200 else {}

    return {
        "name": site_data.get("name", ""),
        "url": site_data.get("URL", ""),
        "username": me_data.get("username", ""),
        "user_id": me_data.get("ID"),
    }


# ---------------------------------------------------------------------------
# Self-hosted validation
# ---------------------------------------------------------------------------


def validate_wordpress_connection(site_url: str, username: str, app_password: str) -> dict:
    """Validate connectivity and credentials for self-hosted WordPress.

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


# ---------------------------------------------------------------------------
# Data fetching (dual-path)
# ---------------------------------------------------------------------------


def fetch_wordpress_data(integration, days: int = 30) -> dict:
    """Fetch WordPress content metrics and trend data for snapshot storage."""
    if _is_wpcom(integration):
        return _fetch_wpcom_data(integration, days)
    return _fetch_selfhosted_data(integration, days)


def _fetch_selfhosted_data(integration, days: int) -> dict:
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

    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=UTC)
    published_posts_30d = sum(
        1
        for post in recent_by_date
        if (_parse_wp_datetime(post.get("date_gmt") or post.get("date")) or datetime.min.replace(tzinfo=UTC))
        >= start_dt
    )
    updated_posts_30d = sum(
        1
        for post in recent_by_modified
        if (
            _parse_wp_datetime(post.get("modified_gmt") or post.get("modified"))
            or datetime.min.replace(tzinfo=UTC)
        )
        >= start_dt
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


def _fetch_wpcom_data(integration, days: int) -> dict:
    access_token = integration.get_access_token()
    headers = _wpcom_auth_header(access_token)
    blog_id = integration.metadata.get("blog_id", "")

    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    posts_url = f"{WPCOM_API_BASE}/sites/{blog_id}/posts"
    all_posts = []
    page_handle = None
    for _ in range(10):
        params = {
            "number": 100,
            "status": "publish",
            "order_by": "date",
            "order": "DESC",
            "fields": "ID,title,slug,URL,date,modified",
        }
        if page_handle:
            params["page_handle"] = page_handle
        try:
            resp = requests.get(posts_url, headers=headers, params=params, timeout=30)
            if resp.status_code != 200:
                break
            data = resp.json()
            posts = data.get("posts", [])
            if not posts:
                break
            all_posts.extend(posts)
            meta = data.get("meta", {})
            next_page = meta.get("next_page")
            if not next_page:
                break
            page_handle = next_page
        except requests.RequestException:
            break

    total_posts = len(all_posts)

    pages_url = f"{WPCOM_API_BASE}/sites/{blog_id}/pages"
    try:
        pages_resp = requests.get(
            pages_url, headers=headers, params={"number": 1, "status": "publish"}, timeout=15
        )
        total_pages = pages_resp.json().get("found", 0) if pages_resp.status_code == 200 else 0
    except requests.RequestException:
        total_pages = 0

    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=UTC)
    published_posts_30d = 0
    updated_posts_30d = 0

    for post in all_posts:
        pub_dt = _parse_wp_datetime(post.get("date", ""))
        mod_dt = _parse_wp_datetime(post.get("modified", ""))
        if pub_dt and pub_dt >= start_dt:
            published_posts_30d += 1
        if mod_dt and mod_dt >= start_dt:
            updated_posts_30d += 1

    top_posts = []
    for post in all_posts[:10]:
        top_posts.append(
            {
                "id": post.get("ID"),
                "title": post.get("title", ""),
                "slug": post.get("slug", ""),
                "url": post.get("URL", ""),
                "published_at": post.get("date"),
                "modified_at": post.get("modified"),
            }
        )

    wpcom_posts_for_daily = [
        {"date_gmt": post.get("date", ""), "date": post.get("date", "")} for post in all_posts
    ]
    daily_publishing = _compute_daily_publishing(wpcom_posts_for_daily, start_date, end_date)

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
    url = urljoin(site_url + "/", "wp-json/wp/v2/posts")
    results: list[dict] = []
    page = 1
    per_page = 100

    while page <= 10:
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


# ---------------------------------------------------------------------------
# Publishing (dual-path)
# ---------------------------------------------------------------------------


def publish_wordpress_post(
    integration,
    title: str,
    content: str,
    excerpt: str = "",
    status: str = "draft",
    slug: str = "",
) -> dict:
    """Publish or save a draft post to WordPress (self-hosted or .com)."""
    if _is_wpcom(integration):
        return _publish_wpcom_post(integration, title, content, excerpt, status, slug)
    return _publish_selfhosted_post(integration, title, content, excerpt, status, slug)


def _publish_selfhosted_post(integration, title, content, excerpt, status, slug) -> dict:
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
        raise ValueError(f"WordPress publish failed (HTTP {resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    return {
        "id": data.get("id"),
        "url": data.get("link"),
        "status": data.get("status"),
        "title": (data.get("title") or {}).get("rendered", title),
    }


def _publish_wpcom_post(integration, title, content, excerpt, status, slug) -> dict:
    access_token = integration.get_access_token()
    headers = _wpcom_auth_header(access_token)
    headers["Content-Type"] = "application/json"
    blog_id = integration.metadata.get("blog_id", "")

    post_url = f"{WPCOM_API_BASE}/sites/{blog_id}/posts/new"
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
        raise ValueError(f"Failed to reach WordPress.com publish API: {exc}") from exc

    if resp.status_code not in (200, 201):
        raise ValueError(f"WordPress.com publish failed (HTTP {resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    return {
        "id": data.get("ID"),
        "url": data.get("URL"),
        "status": data.get("status"),
        "title": data.get("title", title),
    }


def upload_wordpress_media(integration, filename: str, data: bytes, content_type: str) -> dict:
    """Upload an image to the WordPress media library. Returns {id, url}."""
    safe_name = (filename or "image").replace('"', "").strip() or "image"
    if _is_wpcom(integration):
        access_token = integration.get_access_token()
        headers = _wpcom_auth_header(access_token)
        blog_id = integration.metadata.get("blog_id", "")
        url = f"{WPCOM_API_BASE}/sites/{blog_id}/media/new"
        try:
            resp = requests.post(
                url,
                headers=headers,
                files={"media[]": (safe_name, data, content_type)},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ValueError(f"Failed to reach WordPress.com media API: {exc}") from exc
        if resp.status_code not in (200, 201):
            raise ValueError(
                f"WordPress.com media upload failed (HTTP {resp.status_code}): {resp.text[:200]}"
            )
        media = (resp.json().get("media") or [{}])[0]
        return {"id": media.get("ID"), "url": media.get("URL", "")}

    site_url = _normalize_site_url(integration.metadata.get("site_url", ""))
    username = integration.metadata.get("username", "")
    app_password = integration.get_access_token()
    headers = _auth_header(username, app_password)
    headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    headers["Content-Type"] = content_type or "application/octet-stream"

    media_url = urljoin(site_url + "/", "wp-json/wp/v2/media")
    try:
        resp = requests.post(media_url, headers=headers, data=data, timeout=30)
    except requests.RequestException as exc:
        raise ValueError(f"Failed to reach WordPress media API: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise ValueError(f"WordPress media upload failed (HTTP {resp.status_code}): {resp.text[:200]}")
    body = resp.json()
    return {"id": body.get("id"), "url": body.get("source_url", "")}


def delete_wordpress_post(integration, post_id: int) -> None:
    """Delete (trash/force) a post on WordPress (self-hosted or .com)."""
    if _is_wpcom(integration):
        access_token = integration.get_access_token()
        headers = _wpcom_auth_header(access_token)
        blog_id = integration.metadata.get("blog_id", "")
        url = f"{WPCOM_API_BASE}/sites/{blog_id}/posts/{post_id}/delete"
        try:
            resp = requests.post(url, headers=headers, timeout=20)
        except requests.RequestException as exc:
            raise ValueError(f"Failed to reach WordPress.com delete API: {exc}") from exc
    else:
        site_url = _normalize_site_url(integration.metadata.get("site_url", ""))
        username = integration.metadata.get("username", "")
        app_password = integration.get_access_token()
        headers = _auth_header(username, app_password)
        url = urljoin(site_url + "/", f"wp-json/wp/v2/posts/{post_id}")
        try:
            resp = requests.delete(url, headers=headers, params={"force": "true"}, timeout=20)
        except requests.RequestException as exc:
            raise ValueError(f"Failed to reach WordPress delete API: {exc}") from exc

    if resp.status_code not in (200, 201):
        raise ValueError(f"WordPress delete failed (HTTP {resp.status_code}): {resp.text[:200]}")
