"""Central prompt registry (Epic 5).

One diffable source of truth for the platform's LLM prompts. Templates are Jinja2 files
under ``templates/<name>/<version>.j2``; ``MANIFEST`` pins each prompt's current version so
prompts can be iterated and pinned, and Epic 6 can log exactly which version produced a
result.

Usage:
    from apps.analyzer.prompts import render, current_version
    text = render("brand_prompts", count=10, context=ctx, brand_name="Acme")
    ver = current_version("brand_prompts")  # -> "v1"
"""

from .registry import current_version, list_prompts, render

__all__ = ["render", "current_version", "list_prompts"]
