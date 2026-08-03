"""Port for the optional semantic response cache.

``ask_llm(cache=True)`` wants to consult a cache before spending a token, but the
only implementation stores rows in ``analyzer.LLMResponseCache``. Importing that
model from the LLM client is what pinned the client inside ``apps/analyzer`` and
kept three apps importing analyzer just to make an LLM call
(docs/modularization-plan.md §2.6).

Inverted here: ``core`` declares the interface, and whichever app owns the
storage registers an adapter at startup. The client depends on this module; the
implementation depends on the client's app, never the reverse.

Unregistered is a valid state, not an error. With no adapter the calls below are
no-ops, so ``cache=True`` degrades to an ordinary uncached request rather than
failing. That is what lets a worker, a management command or a test boot without
the analyzer app loaded.

Registration lives in ``apps.analyzer.apps.AnalyzerConfig.ready``.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger("apps")


class ResponseCacheBackend(Protocol):
    """What an adapter must provide. Both calls must be best-effort.

    A module exposing matching module-level ``lookup``/``store`` functions
    satisfies this — that is how ``analyzer.pipeline.response_cache`` registers,
    without needing a wrapper class.
    """

    def lookup(self, prompt: str, *, purpose: str, model_key: str, org=None) -> str | None: ...

    def store(self, prompt: str, response: str, *, purpose: str, model_key: str, org=None) -> None: ...


_backend: ResponseCacheBackend | None = None


def register(backend: ResponseCacheBackend) -> None:
    """Install the adapter. Called once, from an AppConfig.ready()."""
    global _backend
    _backend = backend


def reset() -> None:
    """Drop the adapter. For tests that need the unregistered path."""
    global _backend
    _backend = None


def is_registered() -> bool:
    return _backend is not None


def lookup(prompt: str, *, purpose: str, model_key: str, org=None) -> str | None:
    """Cached response, or None on miss, on error, or when unregistered.

    A cache failure must never fail the caller: the fallback is to actually ask
    the model, which is the behaviour the caller would have had anyway.
    """
    if _backend is None:
        return None
    try:
        return _backend.lookup(prompt, purpose=purpose, model_key=model_key, org=org)
    except Exception:
        logger.warning("response cache lookup failed", exc_info=True)
        return None


def store(prompt: str, response: str, *, purpose: str, model_key: str, org=None) -> None:
    """Best-effort write. A failed store is a missed optimisation, not an error."""
    if _backend is None:
        return
    try:
        _backend.store(prompt, response, purpose=purpose, model_key=model_key, org=org)
    except Exception:
        logger.warning("response cache store failed", exc_info=True)
