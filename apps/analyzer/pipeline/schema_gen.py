"""Shared JSON-LD generation helpers (Epic 8).

There used to be two independent JSON-LD generators (auto_fix + geo_improvement) with
their own prompts and their own script-tag handling, which drifted apart. The prompt now
lives in one registry template (``jsonld``) and the wrapping lives here, so both fix paths
emit the same shape.

The LLM call itself stays with each caller: auto_fix and geo_improvement have different
sanitization and error contracts, and collapsing those would change behavior rather than
remove duplication.
"""

from __future__ import annotations

_SCRIPT_OPEN = '<script type="application/ld+json">'
_SCRIPT_CLOSE = "</script>"


def build_jsonld_prompt(*, brand: str, url: str, context: str = "") -> str:
    """Render the single source-of-truth JSON-LD prompt."""
    from ..prompts import render

    return render("jsonld", brand=brand, url=url, context=context or "")


def ensure_script_wrapped(schema: str) -> str:
    """Wrap raw JSON-LD in a script tag unless the model already did."""
    schema = (schema or "").strip()
    if not schema or "<script" in schema.lower():
        return schema
    return f"{_SCRIPT_OPEN}\n{schema}\n{_SCRIPT_CLOSE}"
