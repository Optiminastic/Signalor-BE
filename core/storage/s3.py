"""S3-compatible object storage clients.

Three modules built their own boto3 client: ``accounts.invoice_storage``,
``accounts.profile_storage`` (byte-identical Backblaze B2 setup, copy-pasted) and
``analyzer.blog_store`` (real AWS, different credentials). The B2 pair drifting
apart was a matter of time - a signature-version or addressing-style fix applied
to one and not the other fails only in production, on upload.

Two entry points:

``client()``    - generic, for a caller with its own credentials (blog_store).
``b2_client()`` - the shared Backblaze config, read from ``B2_*`` env.

Clients are cached per credential set. boto3 clients are thread-safe for calls
and moderately expensive to build, so rebuilding one per request is waste; keying
the cache on the credentials means a rotated key produces a new client rather
than silently reusing a stale one.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

# Backblaze B2 accepts any region string; this is the historical default.
B2_DEFAULT_REGION = "us-west-002"


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


@lru_cache(maxsize=8)
def _cached_client(
    endpoint: str,
    key_id: str,
    secret: str,
    region: str,
    path_style: bool,
) -> Any:
    # Imported lazily: boto3 is heavy and not every process touches storage.
    import boto3
    from botocore.config import Config

    kwargs: dict[str, Any] = {
        "aws_access_key_id": key_id,
        "aws_secret_access_key": secret,
        "region_name": region,
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if path_style:
        # Path-style addressing is the safe default for non-AWS S3: virtual-host
        # style needs wildcard DNS the provider may not offer.
        kwargs["config"] = Config(signature_version="s3v4", s3={"addressing_style": "path"})
    return boto3.client("s3", **kwargs)


def client(
    *,
    key_id: str,
    secret: str,
    region: str,
    endpoint: str = "",
    path_style: bool = False,
) -> Any:
    """An S3 client for the given credentials."""
    return _cached_client(endpoint, key_id, secret, region, path_style)


def b2_is_configured() -> bool:
    """Whether Backblaze credentials are present.

    Callers degrade rather than fail: an unconfigured deployment stores nothing
    instead of erroring on every upload.
    """
    return bool(_env("B2_KEY_ID") and _env("B2_APPLICATION_KEY"))


def b2_client() -> Any:
    """The shared Backblaze B2 client."""
    return client(
        key_id=_env("B2_KEY_ID"),
        secret=_env("B2_APPLICATION_KEY"),
        region=_env("B2_REGION") or B2_DEFAULT_REGION,
        endpoint=_env("B2_ENDPOINT"),
        path_style=True,
    )
