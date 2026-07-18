"""Jinja2-backed prompt registry (Epic 5).

Loads versioned prompt templates from ``templates/<name>/<version>.j2`` and renders them
with a caller-supplied context. ``StrictUndefined`` makes a missing variable fail loudly
(caught by the prompt tests) rather than silently producing a broken prompt. Autoescape is
off because prompts are plain text, not HTML.
"""

from __future__ import annotations

import functools
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .manifest import MANIFEST

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Shared prompt variable: the canonical list of AI engines Signalor targets. Kept in one
# place so every prompt refers to the same set (available in templates as ``AI_ENGINES``).
AI_ENGINES = "ChatGPT, Gemini, Perplexity, or Claude"


@functools.lru_cache(maxsize=1)
def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    env.globals["AI_ENGINES"] = AI_ENGINES
    return env


def current_version(name: str) -> str:
    """The pinned current version for ``name`` (e.g. ``"v1"``). Raises on unknown name."""
    try:
        return MANIFEST[name]
    except KeyError:
        raise KeyError(f"Unknown prompt '{name}'. Registered: {sorted(MANIFEST)}") from None


def render(name: str, *, version: str | None = None, **context) -> str:
    """Render prompt ``name`` (current version unless pinned) with ``context``.

    Raises ``KeyError`` for an unknown name, ``jinja2.TemplateNotFound`` for a missing
    version file, and ``jinja2.UndefinedError`` if the template references a variable the
    caller did not pass.
    """
    ver = version or current_version(name)
    template = _env().get_template(f"{name}/{ver}.j2")
    return template.render(**context).strip()


def list_prompts() -> list[str]:
    """All registered prompt names."""
    return sorted(MANIFEST)
