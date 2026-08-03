"""Memory-safety backstop for list endpoints that return a plain JSON array.

Several list endpoints predate any pagination and serialize *every* row, so response
size and memory grow linearly with a tenant's age — a large tenant can OOM the worker
(the agency-admin task list is the worst case, spanning every brand in the agency).

Full page-envelope pagination would change the response shape and break the current
frontend, so this is a deliberate interim: an opt-in ``?limit`` / ``?offset`` with a
hard cap. The default cap is high enough that no real tenant's behaviour changes today;
it only bounds the pathological case. Proper cursor pagination (with coordinated
frontend changes) remains the real fix.
"""

from __future__ import annotations

# High enough that essentially no current tenant is affected, low enough to bound the
# worst-case response. Tune down once the frontend sends explicit ?limit/?offset.
DEFAULT_LIST_LIMIT = 1000
MAX_LIST_LIMIT = 1000


def _int_param(request, name: str, fallback: int) -> int:
    raw = request.query_params.get(name)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value >= 0 else fallback


def bounded_slice(
    request,
    queryset,
    *,
    default_limit: int = DEFAULT_LIST_LIMIT,
    max_limit: int = MAX_LIST_LIMIT,
):
    """Apply a hard-capped ``?limit`` / ``?offset`` to an already-ordered queryset.

    Returns the sliced queryset; callers keep returning a plain list, only the row count
    is bounded. ``limit`` defaults to ``default_limit`` and is capped at ``max_limit``;
    ``offset`` defaults to 0. Invalid/negative values fall back to their defaults.
    """
    limit = min(_int_param(request, "limit", default_limit), max_limit)
    offset = _int_param(request, "offset", 0)
    return queryset[offset : offset + limit]
