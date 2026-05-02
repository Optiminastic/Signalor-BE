"""
Shared HTTP helper for outbound calls to flaky third-party APIs.

Wraps `requests` with retry-on-transient-error + exponential backoff so a
single rate-limit blip or upstream 502 doesn't fail the whole user-facing
operation. Use this for OpenRouter, DataForSEO, Serper, PSI, etc.

Usage:
    from apps.integrations._http import request_with_retry

    resp = request_with_retry(
        "POST",
        "https://api.example.com/v1/foo",
        json={"x": 1},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
"""
from __future__ import annotations

import logging
import random
import time

import requests

logger = logging.getLogger("apps")

# HTTP statuses worth retrying. 429 = Too Many Requests; 5xx variants = upstream
# transient errors. 408 = Request Timeout (some CDNs).
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

# Exceptions worth retrying — purely transport-level transient errors. Don't
# retry SSL errors (often config bugs) or InvalidURL (bug, never works).
RETRY_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def request_with_retry(
    method: str,
    url: str,
    *,
    max_retries: int = 3,
    backoff_base: float = 0.5,
    backoff_cap: float = 8.0,
    timeout: float = 30.0,
    **kwargs,
) -> requests.Response:
    """
    Send an HTTP request, retrying on transient failures.

    Args:
        method:        "GET", "POST", "PUT", "DELETE", "PATCH".
        url:           full URL.
        max_retries:   how many retries beyond the first attempt (default 3 → 4 total tries).
        backoff_base:  initial sleep in seconds; doubles each retry.
        backoff_cap:   maximum sleep per attempt (in case backoff_base × 2^N gets huge).
        timeout:       per-attempt timeout in seconds.
        **kwargs:      passed through to ``requests.request`` (json, data, headers, auth, params).

    Returns:
        The final ``requests.Response`` (could still be a 4xx error — caller
        should call ``.raise_for_status()`` if they want exceptions on 4xx).

    Raises:
        ``requests.RequestException`` if every attempt failed with a
        transient transport-level error.
    """
    method = method.upper()
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            resp = requests.request(method, url, timeout=timeout, **kwargs)
        except RETRY_EXCEPTIONS as exc:
            last_exc = exc
            if attempt >= max_retries:
                logger.warning(
                    "%s %s failed after %d attempts: %s",
                    method, url, attempt + 1, exc,
                )
                raise
            sleep_s = _backoff_with_jitter(attempt, backoff_base, backoff_cap)
            logger.info(
                "%s %s transient error %s; retry %d/%d in %.1fs",
                method, url, type(exc).__name__, attempt + 1, max_retries, sleep_s,
            )
            time.sleep(sleep_s)
            continue

        if resp.status_code in RETRY_STATUSES and attempt < max_retries:
            # Honor Retry-After when present (servers know best on 429).
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            sleep_s = retry_after if retry_after is not None else _backoff_with_jitter(
                attempt, backoff_base, backoff_cap,
            )
            logger.info(
                "%s %s -> %d; retry %d/%d in %.1fs",
                method, url, resp.status_code, attempt + 1, max_retries, sleep_s,
            )
            time.sleep(sleep_s)
            continue

        return resp

    # Defensive — loop above always returns or raises, but keep mypy happy.
    if last_exc is not None:
        raise last_exc
    raise requests.RequestException("request_with_retry: unreachable")


def _backoff_with_jitter(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff (base × 2^attempt) capped, with ±25% jitter."""
    raw = min(base * (2 ** attempt), cap)
    return raw * (0.75 + random.random() * 0.5)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None
