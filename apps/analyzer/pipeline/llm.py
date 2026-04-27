"""
Unified LLM client using OpenRouter.
Routes requests through 3 cheap models: GPT-4o-mini, Claude 3.5 Haiku, Gemini 2.0 Flash.
Falls back to direct Gemini API if no OpenRouter key.
"""
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

logger = logging.getLogger("apps")

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Default models
MODELS = {
    "gpt": "openai/gpt-4o-mini",
    "claude": "anthropic/claude-3.5-haiku",
    "gemini": "google/gemini-2.0-flash-001",
    "perplexity": "perplexity/sonar",
}

MODEL_LABELS = {
    "openai/gpt-4o-mini": "GPT-4o Mini",
    "anthropic/claude-3.5-haiku": "Claude 3.5 Haiku",
    "google/gemini-2.0-flash-001": "Gemini 2.0 Flash",
    "perplexity/sonar": "Perplexity Sonar",
    "gemini-direct": "Gemini 2.0 Flash (Direct)",
}

# Default rotation order
MODEL_ORDER = ["gemini", "gpt", "claude"]

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


def _log_call(model: str, purpose: str, prompt: str, response: str, status: str, duration_ms: int):
    """Record an LLM call to the shared log (thread-safe)."""
    with _log_lock:
        if _collected_logs is None:
            return  # Not collecting

        label = MODEL_LABELS.get(model, model)
        _collected_logs.append({
            "model": label,
            "model_id": model,
            "purpose": purpose,
            "prompt": _sanitize(prompt[:1000]),
            "response": _sanitize(response[:3000]),
            "status": status,
            "duration_ms": duration_ms,
        })


# ── Helpers ───────────────────────────────────────────────────────────────

def _get_openrouter_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY", "").strip() or None


def _get_google_key() -> str | None:
    return os.environ.get("GOOGLE_API_KEY", "").strip() or None


def _pick_model(preferred: str | None = None) -> str:
    """Pick a model. If preferred is set, use that. Otherwise rotate."""
    if preferred and preferred in MODELS:
        return MODELS[preferred]

    global _call_counter
    _call_counter += 1
    provider = MODEL_ORDER[_call_counter % len(MODEL_ORDER)]
    return MODELS[provider]


def is_available() -> bool:
    """Check if any LLM is available."""
    global _availability_cache
    if _availability_cache is not None:
        return _availability_cache

    if _get_openrouter_key():
        _availability_cache = True
        return True

    if _get_google_key():
        _availability_cache = True
        return True

    _availability_cache = False
    logger.warning("No LLM API key found. Set OPENROUTER_API_KEY or GOOGLE_API_KEY in .env")
    return False


# ── Main API ──────────────────────────────────────────────────────────────

def ask_llm(
    prompt: str,
    preferred_provider: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    purpose: str = "",
) -> str:
    """
    Send a prompt to an LLM via OpenRouter, or direct Gemini as fallback.
    Returns response text string. Empty string on failure.
    """
    text, _ = ask_llm_with_citations(
        prompt,
        preferred_provider=preferred_provider,
        max_tokens=max_tokens,
        temperature=temperature,
        purpose=purpose,
    )
    return text


def ask_llm_with_citations(
    prompt: str,
    preferred_provider: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    purpose: str = "",
) -> tuple[str, list[dict]]:
    """
    Send a prompt to an LLM and return (text, citations[]).

    Citations come from provider-specific fields OpenRouter passes through
    (Perplexity `citations`, annotations with `url_citation`, Gemini grounding).
    Empty list when the provider does not attach source metadata.
    """
    if not is_available():
        return ("", [])

    openrouter_key = _get_openrouter_key()

    if openrouter_key:
        return _call_openrouter(prompt, preferred_provider, max_tokens, temperature, openrouter_key, purpose)
    else:
        return (_call_gemini_direct(prompt, purpose), [])


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
        out.append({
            "url": u[:2048],
            "title": (title or "")[:512],
            "snippet": (snippet or "")[:2000],
            "position": len(out) + 1,
        })

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


def _call_openrouter(
    prompt: str, preferred_provider: str | None,
    max_tokens: int, temperature: float, api_key: str,
    purpose: str = "",
) -> tuple[str, list[dict]]:
    """Call OpenRouter API. Returns (text, citations[])."""
    model = _pick_model(preferred_provider)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://optiminastic.com",
        "X-Title": "GEO Analyzer",
    }

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    prompt_preview = _log_preview(prompt, 120)
    logger.info("[LLM REQUEST] >> %s | %s | prompt: \"%s...\"", model, purpose, prompt_preview)

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
            response_preview = _log_preview(content, 200)
            logger.info(
                "[LLM RESPONSE] << %s | %dms | %d chars | %d citations | \"%s...\"",
                model, duration_ms, len(content), len(citations), response_preview,
            )
            _log_call(model, purpose, prompt, content.strip(), "success", duration_ms)
            return (content.strip(), citations)

        logger.warning("[LLM FAILED] << %s | HTTP %d: %s", model, resp.status_code, resp.text[:200])
        _log_call(model, purpose, prompt, f"HTTP {resp.status_code}", "error", duration_ms)
        return _retry_with_next(prompt, model, max_tokens, temperature, api_key, headers, purpose)

    except requests.Timeout:
        duration_ms = int((time.time() - t0) * 1000)
        logger.warning("OpenRouter timeout for %s", model)
        _log_call(model, purpose, prompt, "Timeout", "error", duration_ms)
        return _retry_with_next(prompt, model, max_tokens, temperature, api_key, headers, purpose)
    except Exception as exc:
        duration_ms = int((time.time() - t0) * 1000)
        logger.warning("OpenRouter error for %s: %s", model, exc)
        _log_call(model, purpose, prompt, str(exc), "error", duration_ms)
        return ("", [])


def _retry_with_next(
    prompt: str, failed_model: str, max_tokens: int, temperature: float,
    api_key: str, headers: dict, purpose: str = "",
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
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
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


def _call_gemini_direct(prompt: str, purpose: str = "") -> str:
    """Direct Gemini API call -- used when no OpenRouter key is set."""
    google_key = _get_google_key()
    if not google_key:
        return ""

    prompt_preview = _log_preview(prompt, 120)
    logger.info("[LLM REQUEST] >> gemini-direct | %s | prompt: \"%s...\"", purpose, prompt_preview)

    t0 = time.time()
    try:
        import google.generativeai as genai

        genai.configure(api_key=google_key)
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(prompt, generation_config={"temperature": 0.0})
        text = response.text.strip()
        duration_ms = int((time.time() - t0) * 1000)
        response_preview = _log_preview(text, 200)
        logger.info("[LLM RESPONSE] << gemini-direct | %dms | %d chars | \"%s...\"", duration_ms, len(text), response_preview)
        _log_call("gemini-direct", purpose, prompt, text, "success", duration_ms)
        return text
    except Exception as exc:
        duration_ms = int((time.time() - t0) * 1000)
        logger.warning("[LLM FAILED] << gemini-direct | %s", exc)
        _log_call("gemini-direct", purpose, prompt, str(exc), "error", duration_ms)
        return ""


def ask_multiple_llms(prompt: str, providers: list[str] | None = None, purpose: str = "", max_tokens: int = 512) -> dict[str, str]:
    """
    Ask the same prompt to multiple LLMs IN PARALLEL and return all responses.
    Useful for AI visibility probes -- test across providers concurrently.

    Returns: {"gpt": "response...", "claude": "response...", "gemini": "response..."}
    """
    rich = ask_multiple_llms_with_citations(prompt, providers=providers, purpose=purpose, max_tokens=max_tokens)
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
            prompt, preferred_provider=provider, purpose=purpose, max_tokens=max_tokens,
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

