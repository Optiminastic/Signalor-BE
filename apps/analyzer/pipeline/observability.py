"""Langfuse tracing for every LLM call.

Answers, per call and per run: which model, for what purpose, with what system
prompt and input, what came back, how long it took, how many tokens, and the
exact USD charge. ``run.llm_logs`` already stores this inside the run row; this
module ships the same facts somewhere they can be searched, grouped and charted
across runs and customers.

**Two rules govern everything here.**

1. *Tracing must never break a run.* Every public function swallows its own
   exceptions. A Langfuse outage, a bad key, a network blip - none of it may
   surface to the analysis pipeline. Observability that can take down the thing
   it observes is worse than no observability.

2. *Cost is reported, never inferred.* Langfuse can estimate cost from its own
   model price table, but the models here are served through OpenRouter, which
   returns the real charge on every response. That figure is passed through
   explicitly as ``cost_details`` so the dashboard shows what was actually
   billed rather than a lookup against a price list that may not even contain
   the model.

Trace grouping works without OpenTelemetry context propagation. LLM calls run
inside ``ThreadPoolExecutor`` workers, and OTel context does not cross threads,
so a trace id is derived deterministically from the run id via
``create_trace_id(seed=...)`` and attached explicitly to each generation. Any
thread can therefore file its call under the right run.

Disabled entirely unless ``LANGFUSE_PUBLIC_KEY`` and ``LANGFUSE_SECRET_KEY`` are
set, so local and CI runs cost nothing and need no configuration.
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger("apps")

# Truncation limits. Traces are for diagnosis, not archival: the full prompt and
# response already live in ``run.llm_logs``. Shipping unbounded page content
# would inflate every payload for no diagnostic gain.
_MAX_INPUT_CHARS = 8000
_MAX_OUTPUT_CHARS = 8000

_client_lock = threading.Lock()
_client = None
_client_initialized = False

# Current run context, mirroring how ``llm._collected_logs`` works: a module-level
# value guarded by a lock, so worker threads spawned mid-run see it too.
_context_lock = threading.Lock()
_run_context: dict | None = None


def is_enabled() -> bool:
    return bool(
        os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        and os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    )


def _get_client():
    """Lazily build the singleton client. Returns ``None`` when unusable.

    The failure is cached: if the SDK is missing or the credentials are wrong we
    must not retry the import and constructor on every one of the ~100 LLM calls
    in a run.
    """
    global _client, _client_initialized

    if _client_initialized:
        return _client
    with _client_lock:
        if _client_initialized:
            return _client
        _client_initialized = True
        if not is_enabled():
            return None
        try:
            from langfuse import Langfuse

            _client = Langfuse(
                public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
                secret_key=os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
                # The user-facing env var is LANGFUSE_BASE_URL; LANGFUSE_HOST is
                # the SDK's own name and is accepted as an alias.
                host=(
                    os.getenv("LANGFUSE_BASE_URL", "").strip()
                    or os.getenv("LANGFUSE_HOST", "").strip()
                    or None
                ),
                environment=os.getenv("LANGFUSE_ENVIRONMENT", "").strip() or None,
                release=os.getenv("LANGFUSE_RELEASE", "").strip() or None,
            )
            logger.info("Langfuse tracing enabled")
        except Exception:
            logger.warning("Langfuse client unavailable; tracing disabled", exc_info=True)
            _client = None
        return _client


def start_run(
    run_id,
    url: str = "",
    organization: str = "",
    *,
    user_id: str = "",
    session_id: str = "",
    tags: list[str] | None = None,
    extra: dict | None = None,
) -> None:
    """Bind subsequent LLM calls to one analysis run's trace.

    Called alongside ``llm.start_log_collection()`` so both records describe the
    same span of work.

    ``user_id`` is the account the spend belongs to and ``session_id`` groups
    every run for one organization. Both feed Langfuse's Users and Sessions
    views, which is what turns a pile of traces into "this customer cost us
    $X this month".
    """
    if not is_enabled():
        return
    try:
        client = _get_client()
        if client is None:
            return
        trace_id = client.create_trace_id(seed=f"analysis-run-{run_id}")
        with _context_lock:
            global _run_context
            _run_context = {
                "trace_id": trace_id,
                "run_id": str(run_id),
                "url": url,
                "organization": organization,
                "user_id": user_id or "",
                "session_id": session_id or "",
                "tags": list(tags or []),
                "extra": extra or {},
            }
    except Exception:
        logger.warning("Langfuse start_run failed", exc_info=True)


def end_run() -> None:
    """Clear the run context and push buffered events.

    The explicit flush matters for Celery: a worker that finishes a task and goes
    idle may hold events in its buffer for a while, and a worker that is
    redeployed mid-buffer loses them.
    """
    with _context_lock:
        global _run_context
        _run_context = None
    try:
        client = _get_client()
        if client is not None:
            client.flush()
    except Exception:
        logger.warning("Langfuse flush failed", exc_info=True)


def _current_context() -> dict | None:
    with _context_lock:
        return dict(_run_context) if _run_context else None


def record_generation(
    *,
    model: str,
    purpose: str,
    prompt: str,
    response: str,
    status: str,
    duration_ms: int,
    usage: dict | None = None,
    system: str | None = None,
    web_search: str | None = None,
) -> None:
    """File one LLM call as a Langfuse generation. Never raises."""
    if not is_enabled():
        return
    try:
        client = _get_client()
        if client is None:
            return

        usage = usage or {}
        ctx = _current_context() or {}

        metadata = {
            "purpose": purpose,
            "status": status,
            "duration_ms": duration_ms,
            # The dominant cost driver on answer-engine calls, so it must be
            # filterable rather than buried in the prompt.
            "web_search": web_search or "none",
            "run_id": ctx.get("run_id", ""),
            "url": ctx.get("url", ""),
            "organization": ctx.get("organization", ""),
            "cached_tokens": usage.get("cached_tokens", 0),
            "reasoning_tokens": usage.get("reasoning_tokens", 0),
            **(ctx.get("extra") or {}),
        }

        # A system prompt is part of the input, not a footnote: shipping it
        # separately is what makes "why did this answer change" answerable.
        payload_input = {"prompt": (prompt or "")[:_MAX_INPUT_CHARS]}
        if system:
            payload_input["system"] = system[:_MAX_INPUT_CHARS]

        usage_details = {
            k: int(usage.get(v, 0) or 0)
            for k, v in (("input", "prompt_tokens"), ("output", "completion_tokens"), ("total", "total_tokens"))
        }
        cost = float(usage.get("cost", 0.0) or 0.0)

        kwargs = {
            "name": purpose or "llm-call",
            "as_type": "generation",
            "input": payload_input,
            "output": (response or "")[:_MAX_OUTPUT_CHARS],
            "model": model,
            "metadata": metadata,
            "usage_details": usage_details,
            "level": "DEFAULT" if status == "success" else "ERROR",
        }
        if status != "success":
            kwargs["status_message"] = (response or status)[:500]
        # Only send a cost when the provider actually reported one. Sending zero
        # would read as "this call was free" rather than "cost unknown".
        if cost > 0:
            kwargs["cost_details"] = {"total": cost}
        if ctx.get("trace_id"):
            from langfuse.types import TraceContext

            kwargs["trace_context"] = TraceContext(trace_id=ctx["trace_id"])

        # Trace-level attributes (user, session, name) must be applied *around*
        # the observation: propagate_attributes stamps them onto spans created
        # inside its context, and it works with an explicit trace_context, so it
        # survives the ThreadPoolExecutor workers these calls run in.
        from langfuse import propagate_attributes

        with propagate_attributes(
            user_id=ctx.get("user_id") or None,
            session_id=ctx.get("session_id") or None,
            trace_name=f"analysis-run-{ctx['run_id']}" if ctx.get("run_id") else None,
            tags=ctx.get("tags") or None,
        ):
            generation = client.start_observation(**kwargs)
            generation.end()
    except Exception:
        # Deliberately swallowed: see rule 1 in the module docstring.
        logger.debug("Langfuse record_generation failed", exc_info=True)
