"""
One OpenAI-style tool-calling round-trip through OpenRouter, for the chat agent.

``main`` removed the shared ``ask_llm_with_tools`` along with the old GitHub
agent loop, so the Growth Agent keeps its own copy here, built on the shared
``core.llm.client`` (same API key, model map, Anthropic prompt caching and
call log). Unlike the old version it also records token usage.

The caller owns the message list and the loop. Returns::

    {"message": <assistant message dict, append verbatim>,
     "text": str,
     "tool_calls": [{"id", "name", "arguments": dict}],
     "finish_reason": str}   # "no_key" / "error" when the call could not be made
"""

from __future__ import annotations

import json
import logging
import time

import requests

from core.llm.client import (
    MODELS,
    OPENROUTER_API_URL,
    _extract_usage,
    _get_openrouter_key,
    _log_call,
    _with_anthropic_cache,
)

logger = logging.getLogger("apps")
TIMEOUT_SECONDS = 120
LOG_PREVIEW_CHARS = 500
DEFAULT_PROVIDER = "sonnet"


def _empty(reason: str) -> dict:
    return {"message": {}, "text": "", "tool_calls": [], "finish_reason": reason}


def _parse_tool_calls(message: dict) -> list[dict]:
    parsed = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (ValueError, TypeError):
            args = {}
        parsed.append({"id": call.get("id", ""), "name": fn.get("name", ""), "arguments": args})
    return parsed


def ask_llm_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    preferred_provider: str = DEFAULT_PROVIDER,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    purpose: str = "",
) -> dict:
    api_key = _get_openrouter_key()
    if not api_key:
        return _empty("no_key")
    model = MODELS.get(preferred_provider) or MODELS[DEFAULT_PROVIDER]
    send_messages, send_tools = messages, tools
    if model.startswith("anthropic/"):
        send_messages, send_tools = _with_anthropic_cache(messages, tools)
    payload = {
        "model": model,
        "messages": send_messages,
        "tools": send_tools,
        "tool_choice": "auto",
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "Signalor Growth Agent",
    }
    preview = str(messages)[:LOG_PREVIEW_CHARS]
    started = time.time()
    try:
        response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.warning("growth chat tool call failed: %s", exc)
        return _empty("error")
    duration_ms = int((time.time() - started) * 1000)
    if response.status_code != 200:
        _log_call(model, purpose, preview, f"HTTP {response.status_code}", "error", duration_ms)
        return _empty("error")
    data = response.json()
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    calls = _parse_tool_calls(message)
    text = (message.get("content") or "").strip()
    _log_call(
        model,
        purpose,
        preview,
        text or f"[{len(calls)} tool_calls]",
        "success",
        duration_ms,
        _extract_usage(data),
    )
    return {
        "message": message,
        "text": text,
        "tool_calls": calls,
        "finish_reason": choice.get("finish_reason", ""),
    }
