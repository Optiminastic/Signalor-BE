"""Shared Serper.dev Google Search client (Epic 8).

One place that talks to Serper, so entity/AI-visibility/prompt-tracking all issue the
same request and handle failure identically.

**The contract that matters:** ``search`` returns ``None`` when Serper is unconfigured or
the call fails. ``None`` means *unknown* -- callers must award **no points** for an unknown,
never a guess. This module exists because the checks it replaced asked an LLM to invent
facts (knowledge panels, Google presence) that it could not possibly know.
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger("apps")

_URL = "https://google.serper.dev/search"
_TIMEOUT = 10


def api_key() -> str | None:
    return os.getenv("SERPER_API_KEY", "").strip() or None


def is_configured() -> bool:
    return api_key() is not None


def search(query: str, *, num: int = 10) -> dict | None:
    """Run a Google search via Serper. Returns the raw JSON response.

    ``None`` = unknown (no key, HTTP error, transport error). Never raises.
    """
    key = api_key()
    if not key:
        logger.info("Serper not configured; search skipped (result: unknown)")
        return None
    if not (query or "").strip():
        return None
    try:
        resp = requests.post(
            _URL,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": query, "num": num},
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            # Serper puts the reason in the body (e.g. {"message": "..."}), so log it.
            logger.warning("Serper search failed: %d %s", resp.status_code, (resp.text or "")[:200])
            return None
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - fail-soft: unknown, not an error
        logger.warning("Serper search error: %s", exc)
        return None
