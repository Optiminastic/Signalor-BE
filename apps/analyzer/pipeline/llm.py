"""
Unified LLM client using OpenRouter.
Routes requests through 3 cheap models: GPT-4o-mini, Claude 3.5 Haiku, Gemini 2.0 Flash.
Falls back to direct Gemini API if no OpenRouter key.
"""

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

logger = logging.getLogger("apps")

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Default models.
# OpenRouter model IDs change over time: "google/gemini-2.0-flash-001" was
# delisted (HTTP 404 "No endpoints found"), so we route to 2.5-flash. Override
# via OPENROUTER_GEMINI_MODEL if it's delisted again. Keep in sync with
# apps/analyzer/auto_fix.py.
GEMINI_MODEL = os.getenv("OPENROUTER_GEMINI_MODEL", "google/gemini-2.5-flash")
# Claude Opus — used for high-quality generation (blog idea/title/content). Routed
# through OpenRouter like every other provider (no direct Anthropic SDK). The id is
# hardcoded so it works out of the box; if this ever stops working on your
# OpenRouter account, set OPENROUTER_OPUS_MODEL in the env and that value is used
# instead.
_OPUS_MODEL_DEFAULT = "anthropic/claude-opus-4.1"
OPUS_MODEL = os.getenv("OPENROUTER_OPUS_MODEL", "").strip() or _OPUS_MODEL_DEFAULT
# Claude Sonnet — routed through OpenRouter. Override via OPENROUTER_SONNET_MODEL.
SONNET_MODEL = os.getenv("OPENROUTER_SONNET_MODEL", "anthropic/claude-sonnet-4.5")
# Claude Haiku — the fast "claude" engine for Prompt Track. The old
# ``claude-3.5-haiku`` slug was retired on OpenRouter (HTTP 404 "No endpoints
# found"); ``claude-haiku-4.5`` is the current, available id. Override via
# OPENROUTER_HAIKU_MODEL.
HAIKU_MODEL = os.getenv("OPENROUTER_HAIKU_MODEL", "").strip() or "anthropic/claude-haiku-4.5"
MODELS = {
    "gpt": "openai/gpt-4o-mini",
    "claude": HAIKU_MODEL,
    "opus": OPUS_MODEL,
    "gemini": GEMINI_MODEL,
    "perplexity": "perplexity/sonar",
    "sonnet": SONNET_MODEL,
}

MODEL_LABELS = {
    "openai/gpt-4o-mini": "GPT-4o Mini",
    HAIKU_MODEL: "Claude Haiku 4.5",
    OPUS_MODEL: "Claude Opus",
    GEMINI_MODEL: "Gemini 2.5 Flash",
    "perplexity/sonar": "Perplexity Sonar",
    SONNET_MODEL: "Claude Sonnet 4.5",
    "gemini-direct": "Gemini (Direct)",
}

# Default rotation order
MODEL_ORDER = ["gemini", "gpt", "claude"]

# Model tiers (Cheap / Medium / Strong). Values are MODELS nicknames, so tiers
# reuse the same model-id + OPENROUTER_*_MODEL env plumbing (one source of truth).
# Opus is intentionally not a tier default -- reach it via preferred_provider="opus".
TIERS = {
    "cheap": os.getenv("LLM_TIER_CHEAP", "gemini"),  # google/gemini-2.5-flash
    "medium": os.getenv("LLM_TIER_MEDIUM", "claude"),  # anthropic/claude-haiku-4.5
    "strong": os.getenv("LLM_TIER_STRONG", "sonnet"),  # anthropic/claude-sonnet-4.5
}

_call_counter = 0

# Cache availability check so we don't re-check every call
_availability_cache = None

# ── Thread-safe log collector ─────────────────────────────────────────────
# Uses a global list protected by a lock so worker threads (ThreadPoolExecutor)
# can also append logs during parallel LLM calls.

_log_lock = threading.Lock()
_collected_logs: list[dict] | None = None


def start_log_collection():
    """Start collecting LLM logs (thread-safe, works across ThreadPoolExecutor)."""
    global _collected_logs
    with _log_lock:
        _collected_logs = []


def get_collected_logs() -> list[dict]:
    """Get all collected LLM logs and clear."""
    global _collected_logs
    with _log_lock:
        logs = _collected_logs or []
        _collected_logs = None
        return logs


def _sanitize(text: str) -> str:
    """Remove null bytes and other chars PostgreSQL JSON can't store."""
    return text.replace("\x00", "").encode("utf-8", errors="replace").decode("utf-8")


def _log_preview(text: str, limit: int = 200) -> str:
    """
    Build a console-safe preview string.
    Uses ASCII with backslash escapes so Windows cp1252 logging never crashes.
    """
    compact = _sanitize(text[:limit]).replace("\n", " ").replace("\r", " ")
    return compact.encode("ascii", errors="backslashreplace").decode("ascii")


def _log_call(
    model: str,
    purpose: str,
    prompt: str,
    response: str,
    status: str,
    duration_ms: int,
    usage: dict | None = None,
):
    """Record an LLM call to the shared log (thread-safe).

    ``usage`` is the provider's token-usage block ({prompt_tokens, completion_tokens,
    total_tokens}) when available, so Epic 6 can measure token cost per generation.
    """
    with _log_lock:
        if _collected_logs is None:
            return  # Not collecting

        label = MODEL_LABELS.get(model, model)
        _collected_logs.append(
            {
                "model": label,
                "model_id": model,
                "purpose": purpose,
                "prompt": _sanitize(prompt[:1000]),
                "response": _sanitize(response[:3000]),
                "status": status,
                "duration_ms": duration_ms,
                "usage": usage or {},
            }
        )


# ── Helpers ───────────────────────────────────────────────────────────────


def _get_openrouter_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY", "").strip() or None


def _get_google_key() -> str | None:
    return os.environ.get("GOOGLE_API_KEY", "").strip() or None


def _pick_model(preferred: str | None = None, tier: str | None = None) -> str:
    """Pick a model. Precedence: explicit ``preferred`` nickname (back-compat) ->
    ``tier`` (cheap/medium/strong) -> round-robin rotation."""
    if preferred and preferred in MODELS:
        return MODELS[preferred]

    if tier and tier in TIERS:
        nickname = TIERS[tier]
        if nickname in MODELS:
            return MODELS[nickname]

    global _call_counter
    _call_counter += 1
    provider = MODEL_ORDER[_call_counter % len(MODEL_ORDER)]
    return MODELS[provider]


def _supports_json_object(model: str) -> bool:
    """Whether a model id accepts OpenRouter ``response_format={"type":"json_object"}``.
    Anthropic models commonly reject/ignore it, so we only send it to OpenAI/Gemini
    and keep prompt + Pydantic validation as the real correctness gate."""
    return model.startswith(("openai/", "google/"))


def is_available() -> bool:
    """Check if any LLM is available."""
    global _availability_cache
    if _availability_cache:
        return True

    if _get_openrouter_key():
        _availability_cache = True
        return True

    if _get_google_key():
        _availability_cache = True
        return True

    logger.warning("No LLM API key found. Set OPENROUTER_API_KEY or GOOGLE_API_KEY in .env")
    return False


# ── Main API ──────────────────────────────────────────────────────────────


def ask_llm(
    prompt: str,
    preferred_provider: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    purpose: str = "",
    *,
    system: str | None = None,
    tier: str | None = None,
    response_format: dict | None = None,
) -> str:
    """
    Send a prompt to an LLM via OpenRouter, or direct Gemini as fallback.
    Returns response text string. Empty string on failure.

    Optional keyword-only extras (omitting them reproduces the previous payload):
      system:          system-role instruction sent ahead of the user prompt.
      tier:            "cheap" | "medium" | "strong" model routing (see TIERS).
      response_format: OpenAI-style dict, e.g. {"type": "json_object"} (best-effort;
                       only forwarded to models that support it).
    """
    text, _ = ask_llm_with_citations(
        prompt,
        preferred_provider=preferred_provider,
        max_tokens=max_tokens,
        temperature=temperature,
        purpose=purpose,
        system=system,
        tier=tier,
        response_format=response_format,
    )
    return text


def ask_llm_with_citations(
    prompt: str,
    preferred_provider: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    purpose: str = "",
    *,
    system: str | None = None,
    tier: str | None = None,
    response_format: dict | None = None,
) -> tuple[str, list[dict]]:
    """
    Send a prompt to an LLM and return (text, citations[]).

    Citations come from provider-specific fields OpenRouter passes through
    (Perplexity `citations`, annotations with `url_citation`, Gemini grounding).
    Empty list when the provider does not attach source metadata.

    See ``ask_llm`` for the keyword-only ``system`` / ``tier`` / ``response_format`` extras.
    """
    if not is_available():
        return ("", [])

    openrouter_key = _get_openrouter_key()

    if openrouter_key:
        return _call_openrouter(
            prompt,
            preferred_provider,
            max_tokens,
            temperature,
            openrouter_key,
            purpose,
            system=system,
            tier=tier,
            response_format=response_format,
        )
    else:
        return (
            _call_gemini_direct(
                prompt, purpose, system=system, temperature=temperature, response_format=response_format
            ),
            [],
        )


def _cache_last_block(msg: dict) -> dict:
    """Return a copy of an OpenAI-style message with an ephemeral cache_control
    breakpoint on its final content block (Anthropic caches the prefix up to and
    including it). Handles both string and structured-list content."""
    m = dict(msg)
    content = m.get("content")
    mark = {"type": "ephemeral"}
    if isinstance(content, str):
        if not content:
            return m  # nothing to cache (e.g. assistant tool_calls stub)
        m["content"] = [{"type": "text", "text": content, "cache_control": mark}]
    elif isinstance(content, list) and content:
        last = content[-1]
        last = dict(last) if isinstance(last, dict) else {"type": "text", "text": str(last)}
        last["cache_control"] = mark
        m["content"] = list(content[:-1]) + [last]
    return m


def _with_anthropic_cache(messages: list[dict], tools: list[dict]) -> tuple[list[dict], list[dict]]:
    """Add ephemeral cache breakpoints so a multi-round Anthropic tool loop
    re-reads its stable prefix (tools + system + prior turns) from cache.

    Two breakpoints (Anthropic allows up to 4): the system message (caches
    tools+system) and the last message with content (caches the running
    conversation, which is where the big re-sent file reads accumulate).
    """
    msgs = list(messages)
    # system prefix
    if msgs and msgs[0].get("role") == "system":
        msgs[0] = _cache_last_block(msgs[0])
    # running conversation tail (skip content-less assistant tool_calls stubs)
    if len(msgs) > 1 and msgs[-1].get("content"):
        msgs[-1] = _cache_last_block(msgs[-1])
    # cache the (stable, sizeable) tool definitions too — mark the last one
    send_tools = tools
    if tools:
        send_tools = list(tools[:-1]) + [{**tools[-1], "cache_control": {"type": "ephemeral"}}]
    return msgs, send_tools


def ask_llm_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    preferred_provider: str = "sonnet",
    max_tokens: int = 4096,
    temperature: float = 0.0,
    purpose: str = "",
) -> dict:
    """One tool-calling round-trip via OpenRouter (OpenAI-compatible function calling).

    The caller owns the ``messages`` list and the agent loop; this just sends one
    request and returns what the model said. Returns::

        {
          "message": <raw assistant message dict>,   # append verbatim to messages
          "text": str,                                # assistant content (may be "")
          "tool_calls": [{"id", "name", "arguments": <parsed dict>}],
          "finish_reason": str,
        }

    Requires ``OPENROUTER_API_KEY`` — tool calling isn't available on the direct
    Gemini fallback, so a missing key yields ``finish_reason="no_key"``.
    """
    import json as _json

    api_key = _get_openrouter_key()
    if not api_key:
        return {"message": {}, "text": "", "tool_calls": [], "finish_reason": "no_key"}

    model = MODELS.get(preferred_provider) or MODELS["sonnet"]
    # Anthropic prompt caching: a tool-loop re-sends the same tools + system
    # prompt + already-read files as fresh input on every round. Marking the
    # stable prefix with ephemeral cache_control lets Anthropic bill those
    # repeated tokens at ~10%. Pure cost win, no behaviour change; only applied
    # to Anthropic models (OpenRouter ignores the field for other providers, but
    # we gate anyway to be safe).
    send_messages, send_tools = messages, tools
    if model.startswith("anthropic/"):
        send_messages, send_tools = _with_anthropic_cache(messages, tools)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://optiminastic.com",
        "X-Title": "GEO Fix Agent",
    }
    payload = {
        "model": model,
        "messages": send_messages,
        "tools": send_tools,
        "tool_choice": "auto",
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    t0 = time.time()
    try:
        resp = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=120)
        duration_ms = int((time.time() - t0) * 1000)
    except Exception as exc:
        logger.warning("[LLM TOOLS ERROR] %s: %s", model, exc)
        return {"message": {}, "text": "", "tool_calls": [], "finish_reason": "error"}

    if resp.status_code != 200:
        logger.warning("[LLM TOOLS FAILED] %s HTTP %d: %s", model, resp.status_code, resp.text[:200])
        _log_call(
            model, purpose, _log_preview(str(messages), 500), f"HTTP {resp.status_code}", "error", duration_ms
        )
        return {"message": {}, "text": "", "tool_calls": [], "finish_reason": "error"}

    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message", {}) or {}
    parsed: list[dict] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {}) or {}
        try:
            args = _json.loads(fn.get("arguments") or "{}")
        except (ValueError, TypeError):
            args = {}
        parsed.append({"id": tc.get("id", ""), "name": fn.get("name", ""), "arguments": args})

    text = (msg.get("content") or "").strip()
    _log_call(
        model,
        purpose,
        _log_preview(str(messages), 500),
        text or f"[{len(parsed)} tool_calls]",
        "success",
        duration_ms,
    )
    return {
        "message": msg,
        "text": text,
        "tool_calls": parsed,
        "finish_reason": choice.get("finish_reason", ""),
    }


def _extract_usage(data: dict) -> dict:
    """Pull the token-usage block from an OpenRouter/OpenAI-style response (Epic 6).

    Returns ``{prompt_tokens, completion_tokens, total_tokens}`` (zeros if absent).
    """
    usage = data.get("usage") or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }


def _extract_citations_from_openrouter(data: dict) -> list[dict]:
    """
    Pull structured citations from an OpenRouter JSON response.

    Handles three provider shapes OpenRouter passes through:
      1. Perplexity: top-level `citations: [url, url, ...]` (list of strings).
      2. `:online` / web-search models: `choices[0].message.annotations[]`
         with entries like {type: "url_citation", url_citation: {url, title, content}}.
      3. Gemini grounding: sometimes surfaces in `choices[0].message.grounding_metadata`
         (`grounding_chunks[].web.uri`).

    Deduplicated by URL in first-seen order. Returns [{url, title, snippet, position}].
    """
    from urllib.parse import urlparse

    out: list[dict] = []
    seen: set[str] = set()

    def _add(url: str, title: str = "", snippet: str = "") -> None:
        if not isinstance(url, str):
            return
        u = url.strip()
        if not u or not u.startswith(("http://", "https://")):
            return
        if u in seen:
            return
        try:
            if not urlparse(u).netloc:
                return
        except Exception:
            return
        seen.add(u)
        out.append(
            {
                "url": u[:2048],
                "title": (title or "")[:512],
                "snippet": (snippet or "")[:2000],
                "position": len(out) + 1,
            }
        )

    try:
        # 1. Perplexity — top-level citations array (list of URL strings)
        top_cites = data.get("citations")
        if isinstance(top_cites, list):
            for c in top_cites:
                if isinstance(c, str):
                    _add(c)
                elif isinstance(c, dict):
                    _add(c.get("url", ""), c.get("title", ""), c.get("snippet") or c.get("content", ""))

        # 2. Annotations on the assistant message (OpenAI :online, web-search models)
        message = (data.get("choices") or [{}])[0].get("message", {}) or {}
        annotations = message.get("annotations") or []
        if isinstance(annotations, list):
            for ann in annotations:
                if not isinstance(ann, dict):
                    continue
                if ann.get("type") == "url_citation" and isinstance(ann.get("url_citation"), dict):
                    uc = ann["url_citation"]
                    _add(uc.get("url", ""), uc.get("title", ""), uc.get("content", ""))
                elif ann.get("type") == "url" and ann.get("url"):
                    _add(ann.get("url", ""), ann.get("title", ""), ann.get("snippet", ""))

        # 3. Gemini-style grounding metadata (occasionally passed through)
        grounding = message.get("grounding_metadata") or message.get("groundingMetadata")
        if isinstance(grounding, dict):
            chunks = grounding.get("grounding_chunks") or grounding.get("groundingChunks") or []
            for ch in chunks:
                web = (ch or {}).get("web") or {}
                _add(web.get("uri", ""), web.get("title", ""))
    except Exception as exc:
        logger.debug("citation extraction failed: %s", exc)

    return out


def _build_messages(prompt: str, system: str | None) -> list[dict]:
    """OpenAI-style message list, with an optional leading system message."""
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def _call_openrouter(
    prompt: str,
    preferred_provider: str | None,
    max_tokens: int,
    temperature: float,
    api_key: str,
    purpose: str = "",
    *,
    system: str | None = None,
    tier: str | None = None,
    response_format: dict | None = None,
) -> tuple[str, list[dict]]:
    """Call OpenRouter API. Returns (text, citations[])."""
    model = _pick_model(preferred_provider, tier)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://optiminastic.com",
        "X-Title": "GEO Analyzer",
    }

    payload = {
        "model": model,
        "messages": _build_messages(prompt, system),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # Best-effort JSON mode: only send to models that accept it so Anthropic
    # never 400s. Correctness is enforced downstream by Pydantic validation.
    if response_format and _supports_json_object(model):
        payload["response_format"] = response_format

    prompt_preview = _log_preview(prompt, 120)
    logger.info('[LLM REQUEST] >> %s | %s | prompt: "%s..."', model, purpose, prompt_preview)

    t0 = time.time()
    try:
        resp = requests.post(
            OPENROUTER_API_URL,
            headers=headers,
            json=payload,
            timeout=30,
        )
        duration_ms = int((time.time() - t0) * 1000)

        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            citations = _extract_citations_from_openrouter(data)
            usage = _extract_usage(data)
            response_preview = _log_preview(content, 200)
            logger.info(
                '[LLM RESPONSE] << %s | %dms | %d chars | %d citations | "%s..."',
                model,
                duration_ms,
                len(content),
                len(citations),
                response_preview,
            )
            _log_call(model, purpose, prompt, content.strip(), "success", duration_ms, usage=usage)
            return (content.strip(), citations)

        logger.warning("[LLM FAILED] << %s | HTTP %d: %s", model, resp.status_code, resp.text[:200])
        _log_call(model, purpose, prompt, f"HTTP {resp.status_code}", "error", duration_ms)
        return _retry_with_next(
            prompt,
            model,
            max_tokens,
            temperature,
            api_key,
            headers,
            purpose,
            system=system,
            response_format=response_format,
        )

    except requests.Timeout:
        duration_ms = int((time.time() - t0) * 1000)
        logger.warning("OpenRouter timeout for %s", model)
        _log_call(model, purpose, prompt, "Timeout", "error", duration_ms)
        return _retry_with_next(
            prompt,
            model,
            max_tokens,
            temperature,
            api_key,
            headers,
            purpose,
            system=system,
            response_format=response_format,
        )
    except Exception as exc:
        duration_ms = int((time.time() - t0) * 1000)
        logger.warning("OpenRouter error for %s: %s", model, exc)
        _log_call(model, purpose, prompt, str(exc), "error", duration_ms)
        return ("", [])


def _retry_with_next(
    prompt: str,
    failed_model: str,
    max_tokens: int,
    temperature: float,
    api_key: str,
    headers: dict,
    purpose: str = "",
    *,
    system: str | None = None,
    response_format: dict | None = None,
) -> tuple[str, list[dict]]:
    """Try the next model if the first one fails. Returns (text, citations[])."""
    all_models = list(MODELS.values())
    for model in all_models:
        if model == failed_model:
            continue

        t0 = time.time()
        try:
            payload = {
                "model": model,
                "messages": _build_messages(prompt, system),
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if response_format and _supports_json_object(model):
                payload["response_format"] = response_format
            resp = requests.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=payload,
                timeout=30,
            )
            duration_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                citations = _extract_citations_from_openrouter(data)
                logger.info("Fallback to %s succeeded (%dms)", model, duration_ms)
                _log_call(model, purpose + " (retry)", prompt, content.strip(), "success", duration_ms)
                return (content.strip(), citations)
        except Exception:
            continue

    return ("", [])


def _call_gemini_direct(
    prompt: str,
    purpose: str = "",
    *,
    system: str | None = None,
    temperature: float = 0.0,
    response_format: dict | None = None,
) -> str:
    """Direct Gemini API call -- used when no OpenRouter key is set."""
    google_key = _get_google_key()
    if not google_key:
        return ""

    prompt_preview = _log_preview(prompt, 120)
    logger.info('[LLM REQUEST] >> gemini-direct | %s | prompt: "%s..."', purpose, prompt_preview)

    t0 = time.time()
    try:
        import google.generativeai as genai

        genai.configure(api_key=google_key)
        # system_instruction / response_mime_type are guarded: some installed SDK
        # versions reject them. On TypeError we fall back to prompt-only (with the
        # system text prepended so the instruction is not silently lost).
        gen_config: dict = {"temperature": temperature}
        if response_format:
            gen_config["response_mime_type"] = "application/json"
        try:
            model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=system or None)
            response = model.generate_content(prompt, generation_config=gen_config)
        except TypeError:
            effective_prompt = f"{system}\n\n{prompt}" if system else prompt
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(
                effective_prompt, generation_config={"temperature": temperature}
            )
        text = response.text.strip()
        duration_ms = int((time.time() - t0) * 1000)
        response_preview = _log_preview(text, 200)
        logger.info(
            '[LLM RESPONSE] << gemini-direct | %dms | %d chars | "%s..."',
            duration_ms,
            len(text),
            response_preview,
        )
        _log_call("gemini-direct", purpose, prompt, text, "success", duration_ms)
        return text
    except Exception as exc:
        duration_ms = int((time.time() - t0) * 1000)
        logger.warning("[LLM FAILED] << gemini-direct | %s", exc)
        _log_call("gemini-direct", purpose, prompt, str(exc), "error", duration_ms)
        return ""


def ask_multiple_llms(
    prompt: str, providers: list[str] | None = None, purpose: str = "", max_tokens: int = 512
) -> dict[str, str]:
    """
    Ask the same prompt to multiple LLMs IN PARALLEL and return all responses.
    Useful for AI visibility probes -- test across providers concurrently.

    Returns: {"gpt": "response...", "claude": "response...", "gemini": "response..."}
    """
    rich = ask_multiple_llms_with_citations(
        prompt, providers=providers, purpose=purpose, max_tokens=max_tokens
    )
    return {p: v["text"] for p, v in rich.items()}


def ask_multiple_llms_with_citations(
    prompt: str,
    providers: list[str] | None = None,
    purpose: str = "",
    max_tokens: int = 512,
) -> dict[str, dict]:
    """
    Parallel variant that returns structured {text, citations[]} per provider.

    Returns: {"gpt": {"text": "...", "citations": [{url, title, snippet, position}]}, ...}
    """
    if not is_available():
        return {}

    if providers is None:
        providers = list(MODELS.keys())

    providers = [p for p in providers if p in MODELS]
    if not providers:
        return {}

    # If only direct Gemini is available (no OpenRouter), just use that
    if not _get_openrouter_key():
        text = _call_gemini_direct(prompt, purpose)
        return {"gemini": {"text": text, "citations": []}} if text else {}

    results: dict[str, dict] = {}

    def _call_provider(provider):
        text, citations = ask_llm_with_citations(
            prompt,
            preferred_provider=provider,
            purpose=purpose,
            max_tokens=max_tokens,
        )
        return provider, {"text": text, "citations": citations}

    with ThreadPoolExecutor(max_workers=max(1, len(providers))) as executor:
        futures = {executor.submit(_call_provider, p): p for p in providers}
        for future in as_completed(futures):
            try:
                provider, payload = future.result()
                results[provider] = payload
            except Exception as exc:
                provider = futures[future]
                logger.warning("Parallel LLM call failed for %s: %s", provider, exc)
                results[provider] = {"text": "", "citations": []}

    return results
