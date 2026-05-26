"""
Anti-abuse gate for public AI-call endpoints (generate-prompts, etc).

Pattern:
  1. Anon client visits onboarding, asks /onboarding-start for a token
     (optionally proving they're not a bot via Cloudflare Turnstile).
  2. Server mints a signed, IP-bound, short-lived token using Django's
     built-in signed-payload primitives (SECRET_KEY-backed).
  3. Client sends ``X-Onboarding-Token`` on subsequent expensive calls.
  4. Server verifies signature, age, and IP match.

Why this stops the screenshot attack:
  Per-IP DRF throttles cap one attacker at N req/min. A botnet with rotating
  IPs bypasses that — each fresh IP starts a fresh bucket. The token forces
  every fresh IP through /onboarding-start, which is itself throttled AND
  optionally gated by Turnstile (real human required). So a botnet now needs
  to solve CAPTCHAs at scale, not just rotate IPs.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings
from django.core import signing

logger = logging.getLogger(__name__)

_SALT = "signalor.analyzer.onboarding.v1"
_DEFAULT_MAX_AGE = 900  # 15 min — long enough for the full onboarding flow

_TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def mint_token(client_ip: str) -> str:
    """Return a signed token bound to the caller IP."""
    return signing.dumps({"ip": client_ip}, salt=_SALT)


def verify_token(token: str, client_ip: str, max_age: int | None = None) -> tuple[bool, str]:
    """
    Returns (ok, reason). ``reason`` is empty on success, else a short tag
    suitable for logging / 401 responses (never leaks signing internals).
    """
    if not token:
        return False, "missing"
    try:
        payload = signing.loads(token, salt=_SALT, max_age=max_age or _DEFAULT_MAX_AGE)
    except signing.SignatureExpired:
        return False, "expired"
    except signing.BadSignature:
        return False, "invalid_signature"
    except Exception as exc:  # defensive
        logger.warning("onboarding_token verify error: %s", exc)
        return False, "malformed"

    if not isinstance(payload, dict):
        return False, "malformed"

    bound_ip = payload.get("ip", "")
    if bound_ip and client_ip and bound_ip != client_ip:
        # Token was minted for a different IP. Don't allow lateral reuse
        # across a botnet that scraped one valid token. (Soft-skip if either
        # side is empty so misconfigured proxies don't lock everyone out.)
        return False, "ip_mismatch"

    return True, ""


def turnstile_enabled() -> bool:
    return bool(getattr(settings, "TURNSTILE_SECRET", "") or "")


def verify_turnstile(token: str, client_ip: str) -> tuple[bool, str]:
    """
    Verify a Cloudflare Turnstile token against the siteverify endpoint.
    Returns (ok, reason). When TURNSTILE_SECRET is unset, returns (True, "skipped")
    so dev / staging without a CF account still work.
    """
    secret = getattr(settings, "TURNSTILE_SECRET", "")
    if not secret:
        return True, "skipped"  # not configured — treat as pass
    if not token:
        return False, "missing"

    try:
        resp = requests.post(
            _TURNSTILE_VERIFY_URL,
            data={
                "secret": secret,
                "response": token,
                "remoteip": client_ip,
            },
            timeout=4,
        )
    except requests.RequestException as exc:
        logger.warning("turnstile siteverify network error: %s", exc)
        # Fail open on network errors — better to accept a real user than
        # have the entire signup flow break when Cloudflare is degraded.
        return True, "siteverify_unreachable"

    if not resp.ok:
        logger.warning("turnstile siteverify HTTP %s", resp.status_code)
        return True, "siteverify_http_error"

    data = resp.json() if resp.content else {}
    if data.get("success"):
        return True, ""

    error_codes = data.get("error-codes", [])
    return False, f"turnstile_failed:{','.join(error_codes) or 'unknown'}"
