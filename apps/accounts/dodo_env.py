"""Dodo Payments env helpers (matches official SDK env names where possible)."""

from __future__ import annotations

import os


def normalized_dodo_api_key() -> str:
    """
    Read API secret from DODO_API_KEY or DODO_PAYMENTS_API_KEY (SDK default name).

    Strips accidental ``Bearer `` prefix and outer quotes so ``Authorization`` is not doubled.
    """
    raw = os.getenv("DODO_API_KEY", "") or os.getenv("DODO_PAYMENTS_API_KEY", "")
    key = raw.strip()
    if key.startswith("\ufeff"):
        key = key.lstrip("\ufeff").strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        key = key[1:-1].strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    return key


def dodo_live_mode_enabled() -> bool:
    """True → SDK uses live.dodopayments.com; False → test.dodopayments.com."""
    return os.getenv("DODO_LIVE_MODE", "false").lower() in ("true", "1", "yes")


def dodo_mode_public() -> str:
    """Return ``live`` or ``test`` for API responses (no secrets)."""
    return "live" if dodo_live_mode_enabled() else "test"
