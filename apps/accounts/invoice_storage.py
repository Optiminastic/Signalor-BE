"""Backblaze B2 storage for cached invoice PDFs.

B2 exposes an S3-compatible API; we use ``boto3`` against a custom endpoint
URL. The bucket holds objects keyed ``invoices/{payment_id}.pdf``. All
operations are best-effort: missing env vars or transient B2 errors fall
through silently so the rest of the payment flow keeps working.

Why cache: Dodo's invoice endpoint is the only thing standing between the
user and their PDF. When it 5xx's, or when an API-key/mode mismatch causes
a 404 on a previously valid id, the user is stuck. Once a PDF is fetched
successfully once, we own it in B2 forever — Dodo outages stop mattering.
"""

from __future__ import annotations

import importlib.util
import logging
import os

from core import storage

logger = logging.getLogger("apps")


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def is_b2_enabled() -> bool:
    """True when all four required env vars are present."""
    return all(_env(k) for k in ("B2_KEY_ID", "B2_APPLICATION_KEY", "B2_BUCKET", "B2_ENDPOINT"))


def _client():
    """Boto3 S3 client pointed at B2. Returns None when not configured."""
    if not is_b2_enabled():
        return None
    if importlib.util.find_spec("boto3") is None:
        logger.warning("boto3 not installed; B2 invoice cache disabled")
        return None

    # Shared with profile/invoice storage and any future B2 caller.
    return storage.b2_client()


def _key(payment_id: str) -> str:
    # Strip any path traversal; payment ids are alnum+underscore by convention.
    safe = (payment_id or "").replace("/", "_").replace("..", "_")[:120]
    return f"invoices/{safe}.pdf"


def get_cached_invoice(payment_id: str) -> bytes | None:
    """Return cached PDF bytes, or None if not cached / not configured / error."""
    if not payment_id:
        return None
    client = _client()
    if client is None:
        return None
    bucket = _env("B2_BUCKET")
    try:
        obj = client.get_object(Bucket=bucket, Key=_key(payment_id))
        body = obj.get("Body")
        if body is None:
            return None
        data = body.read()
        if not data or len(data) < 100:
            return None
        return data
    except client.exceptions.NoSuchKey:
        return None
    except Exception as e:
        # boto3 raises ClientError for 404s on some configurations; treat as miss.
        code = getattr(getattr(e, "response", None), "get", lambda *_: {})("Error") or {}
        if (code.get("Code") or "").lower() in ("nosuchkey", "404", "notfound"):
            return None
        logger.warning("B2 get_cached_invoice failed for %s: %s", payment_id, e)
        return None


def cache_invoice(payment_id: str, pdf_bytes: bytes) -> bool:
    """Upload PDF to B2. Returns True on success, False otherwise. Best-effort."""
    if not payment_id or not pdf_bytes:
        return False
    if len(pdf_bytes) < 100:
        return False
    client = _client()
    if client is None:
        return False
    bucket = _env("B2_BUCKET")
    try:
        client.put_object(
            Bucket=bucket,
            Key=_key(payment_id),
            Body=pdf_bytes,
            ContentType="application/pdf",
            # 1-year cache — invoices are immutable once issued.
            CacheControl="public, max-age=31536000, immutable",
        )
        logger.info("B2 cached invoice payment_id=%s (%d bytes)", payment_id, len(pdf_bytes))
        return True
    except Exception as e:
        logger.warning("B2 cache_invoice failed for %s: %s", payment_id, e)
        return False
