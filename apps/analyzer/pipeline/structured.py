"""
Structured-output helpers on top of ``llm.py``.

One place for the "ask the model for JSON, then trust it only after validation"
pattern that was previously copy-pasted (``re.search + json.loads + try/except``)
across ~12 pipeline modules.

Design (see Epic 1 plan):
- The prompt is the contract: we append the target JSON Schema and "return only JSON".
- Native JSON mode is best-effort -- ``llm.py`` forwards ``response_format`` only to
  models that accept it (OpenAI/Gemini); Anthropic gets a plain prompt.
- Pydantic validation is the real correctness gate.
- On failure we do exactly ONE auto-repair round-trip, then fail soft (return
  ``None`` / ``[]``) so callers keep their existing fallbacks.

Pure module: no Django/DB imports.
"""

import json
import logging
import os
import re
from typing import Any, TypeVar

from pydantic import BaseModel, RootModel, ValidationError

from .llm import ask_llm

logger = logging.getLogger("apps")

T = TypeVar("T", bound=BaseModel)

_JSON_OBJECT = {"type": "json_object"}


def _hint_enabled() -> bool:
    """Kill switch: set LLM_STRUCTURED_ENABLED=false to stop sending the provider
    ``response_format`` hint (prompt + Pydantic + repair still apply)."""
    return os.getenv("LLM_STRUCTURED_ENABLED", "true").strip().lower() != "false"


# ── JSON extraction (the single shared parser) ────────────────────────────


def strip_code_fences(text: str) -> str:
    """Idempotently remove a leading ```json / ``` fence and its trailing ```."""
    if not text:
        return ""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _slice_parse(text: str, open_c: str, close_c: str) -> Any | None:
    start, end = text.find(open_c), text.rfind(close_c)
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (ValueError, TypeError):
            return None
    return None


def extract_json(text: str, *, expect: type = dict) -> Any | None:
    """Best-effort parse of a JSON value out of possibly-fenced / chatty LLM text.

    Tries a direct ``json.loads`` first, then falls back to the outermost
    balanced ``{...}`` / ``[...]`` slice (``expect=list`` prefers brackets).
    Returns the parsed value, or ``None`` if nothing parses.
    """
    if not text:
        return None
    cleaned = strip_code_fences(text)
    try:
        return json.loads(cleaned)
    except (ValueError, TypeError):
        pass
    order = (("[", "]"), ("{", "}")) if expect is list else (("{", "}"), ("[", "]"))
    for open_c, close_c in order:
        result = _slice_parse(cleaned, open_c, close_c)
        if result is not None:
            return result
    return None


# ── Prompt construction ───────────────────────────────────────────────────


def _schema_hint(schema: type[BaseModel]) -> str:
    try:
        return json.dumps(schema.model_json_schema(), ensure_ascii=False)
    except Exception:  # pragma: no cover - schema generation should not fail
        return "{}"


def _is_object_schema(schema: type[BaseModel]) -> bool:
    """A plain object schema (safe for provider json_object mode). RootModel
    wrappers (e.g. list roots) are not -- json_object mode requires an object."""
    try:
        return not issubclass(schema, RootModel)
    except TypeError:
        return False


def _object_instruction(schema: type[BaseModel]) -> str:
    return (
        "\n\nReturn ONLY a single valid JSON object matching this JSON Schema. "
        "No markdown, no code fences, no commentary.\nJSON Schema:\n" + _schema_hint(schema)
    )


def _root_instruction(schema: type[BaseModel]) -> str:
    """Instruction for RootModel / non-object schemas (e.g. a JSON array root)."""
    return (
        "\n\nReturn ONLY valid JSON matching this JSON Schema. "
        "No markdown, no code fences, no commentary.\nJSON Schema:\n" + _schema_hint(schema)
    )


def _array_instruction(item_schema: type[BaseModel]) -> str:
    return (
        "\n\nReturn ONLY a valid JSON array of objects, each matching this JSON Schema. "
        "No markdown, no code fences, no commentary.\nItem JSON Schema:\n" + _schema_hint(item_schema)
    )


def _short_error(exc: ValidationError) -> str:
    return str(exc)[:600]


def _repair_prompt(schema_hint: str, raw: str, err: str, *, array: bool) -> str:
    shape = "a JSON array of objects" if array else "a single JSON object"
    return (
        f"Your previous output was supposed to be {shape} matching the schema below, "
        "but it failed to parse/validate. Return corrected JSON ONLY -- no markdown, "
        "no commentary.\n\n"
        f"Schema:\n{schema_hint}\n\n"
        f"Previous output:\n{raw[:4000]}\n\n"
        f"Validation error:\n{err}"
    )


# ── Public API ────────────────────────────────────────────────────────────


def ask_structured(
    prompt: str,
    schema: type[T],
    *,
    system: str | None = None,
    tier: str = "medium",
    max_tokens: int = 1024,
    temperature: float = 0.0,
    purpose: str = "",
    preferred_provider: str | None = None,
    repair: bool = True,
) -> T | None:
    """Ask for a single JSON object and return a validated ``schema`` instance,
    or ``None`` on failure (after one repair attempt). Fail-soft by design."""
    is_object = _is_object_schema(schema)
    rf = _JSON_OBJECT if (_hint_enabled() and is_object) else None
    instruction = _object_instruction(schema) if is_object else _root_instruction(schema)
    raw = ask_llm(
        prompt + instruction,
        preferred_provider=preferred_provider,
        tier=tier,
        max_tokens=max_tokens,
        temperature=temperature,
        purpose=purpose,
        system=system,
        response_format=rf,
    )
    obj, err = _validate_one(schema, raw)
    if obj is not None:
        return obj
    if not raw or not repair:
        if raw:
            logger.warning("ask_structured[%s] failed: %s", purpose or schema.__name__, err)
        return None

    raw2 = ask_llm(
        _repair_prompt(_schema_hint(schema), raw, err, array=False),
        preferred_provider=preferred_provider,
        tier=tier,
        max_tokens=max_tokens,
        temperature=0.0,
        purpose=(purpose + " (repair)").strip(),
        system=system,
        response_format=rf,
    )
    obj2, err2 = _validate_one(schema, raw2)
    if obj2 is None:
        logger.warning("ask_structured[%s] repair failed: %s", purpose or schema.__name__, err2)
    return obj2


def ask_structured_list(
    prompt: str,
    item_schema: type[T],
    *,
    system: str | None = None,
    tier: str = "medium",
    max_tokens: int = 1024,
    temperature: float = 0.0,
    purpose: str = "",
    preferred_provider: str | None = None,
    repair: bool = True,
) -> list[T]:
    """Ask for a JSON array and return a list of validated ``item_schema`` items
    (invalid items are skipped). Returns ``[]`` on total failure. Never sends
    provider json_object mode (that mode requires an object, not an array)."""
    raw = ask_llm(
        prompt + _array_instruction(item_schema),
        preferred_provider=preferred_provider,
        tier=tier,
        max_tokens=max_tokens,
        temperature=temperature,
        purpose=purpose,
        system=system,
    )
    items = _validate_many(item_schema, raw)
    if items is not None:
        return items
    if not raw or not repair:
        if raw:
            logger.warning("ask_structured_list[%s] failed to parse array", purpose or item_schema.__name__)
        return []

    raw2 = ask_llm(
        _repair_prompt(_schema_hint(item_schema), raw, "output was not a JSON array", array=True),
        preferred_provider=preferred_provider,
        tier=tier,
        max_tokens=max_tokens,
        temperature=0.0,
        purpose=(purpose + " (repair)").strip(),
        system=system,
    )
    items2 = _validate_many(item_schema, raw2)
    if items2 is None:
        logger.warning("ask_structured_list[%s] repair failed", purpose or item_schema.__name__)
        return []
    return items2


# ── Validation internals ──────────────────────────────────────────────────


def _validate_one(schema: type[T], raw: str) -> tuple[T | None, str]:
    data = extract_json(raw, expect=dict)
    if data is None:
        return None, "output was not parseable JSON"
    try:
        return schema.model_validate(data), ""
    except ValidationError as exc:
        return None, _short_error(exc)


def _validate_many(item_schema: type[T], raw: str) -> list[T] | None:
    """Parse an array and validate each item. Returns ``None`` only when the
    top-level value is not a JSON array (so the caller can trigger a repair);
    individual invalid items are dropped, not fatal."""
    data = extract_json(raw, expect=list)
    if not isinstance(data, list):
        return None
    out: list[T] = []
    for item in data:
        try:
            out.append(item_schema.model_validate(item))
        except ValidationError:
            continue
    return out
