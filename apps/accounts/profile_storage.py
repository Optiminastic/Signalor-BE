"""Backblaze B2 storage for user-uploaded profile photos.

Mirrors ``invoice_storage`` but for the ``profile-photos/`` prefix. The
bucket is private; reads go through pre-signed URLs with a long expiry
so the frontend can render them in ``<img src>`` without proxying bytes
through Django.
"""

from __future__ import annotations

import logging
import os
import secrets

logger = logging.getLogger("apps")

# Pre-signed URL lifetime. 7 days is the S3 v4 maximum and long enough
# that page loads in the same browsing session always render even after
# a sleep.
_SIGNED_URL_TTL_SECONDS = 7 * 24 * 60 * 60

_ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}

_MAX_BYTES = 2 * 1024 * 1024  # 2 MB — matches the UI hint shown in the upload card.


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def is_b2_enabled() -> bool:
    return all(_env(k) for k in ("B2_KEY_ID", "B2_APPLICATION_KEY", "B2_BUCKET", "B2_ENDPOINT"))


def _client():
    if not is_b2_enabled():
        return None
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        logger.warning("boto3 not installed; B2 profile photo storage disabled")
        return None

    return boto3.client(
        "s3",
        endpoint_url=_env("B2_ENDPOINT"),
        aws_access_key_id=_env("B2_KEY_ID"),
        aws_secret_access_key=_env("B2_APPLICATION_KEY"),
        region_name=_env("B2_REGION") or "us-west-002",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _make_key(user_id: int, ext: str) -> str:
    """Random token in the key prevents URL guessing once an old key is
    deleted — even if the bucket leaks a stale path, it won't resolve."""
    token = secrets.token_urlsafe(12).replace("_", "").replace("-", "")[:12]
    safe_ext = (ext or "jpg").lower().strip(".") or "jpg"
    return f"profile-photos/{user_id}_{token}.{safe_ext}"


def validate_upload(content_type: str, size: int) -> str | None:
    """Return None if the upload is acceptable, otherwise an error string."""
    if size <= 0:
        return "Empty file."
    if size > _MAX_BYTES:
        return "File is larger than 2 MB."
    if (content_type or "").lower() not in _ALLOWED_CONTENT_TYPES:
        return "Unsupported file type. Use JPG, PNG, WEBP, or GIF."
    return None


def upload_photo(user_id: int, data: bytes, content_type: str) -> tuple[str | None, str | None]:
    """Upload ``data`` to B2. Returns ``(object_key, error_tag)``."""
    if not is_b2_enabled():
        return None, "not_configured"
    err = validate_upload(content_type, len(data))
    if err:
        return None, err
    client = _client()
    if client is None:
        return None, "not_configured"
    ext = _ALLOWED_CONTENT_TYPES[content_type.lower()]
    key = _make_key(user_id, ext)
    bucket = _env("B2_BUCKET")
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="public, max-age=86400",
        )
    except Exception as e:
        logger.warning("B2 upload_photo failed for user=%s: %s", user_id, e)
        return None, "upload_failed"
    return key, None


def delete_photo(object_key: str) -> bool:
    """Delete an object. Best-effort — missing keys are treated as success."""
    if not object_key or not is_b2_enabled():
        return False
    client = _client()
    if client is None:
        return False
    bucket = _env("B2_BUCKET")
    try:
        client.delete_object(Bucket=bucket, Key=object_key)
        return True
    except Exception as e:
        logger.warning("B2 delete_photo failed for key=%s: %s", object_key, e)
        return False


def photo_url(object_key: str) -> str | None:
    """Pre-signed read URL for the photo, or None when not configured."""
    if not object_key or not is_b2_enabled():
        return None
    client = _client()
    if client is None:
        return None
    bucket = _env("B2_BUCKET")
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": object_key},
            ExpiresIn=_SIGNED_URL_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning("B2 photo_url failed for key=%s: %s", object_key, e)
        return None
