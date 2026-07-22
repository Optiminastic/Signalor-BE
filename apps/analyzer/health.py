"""Liveness signal for the analysis Celery worker.

The heavy analyze / re-analyze pipeline runs on a dedicated worker consuming the
RabbitMQ ``analysis`` queue (see ``config.celery_rabbit``). The general
``/health/`` check only proves the web process + its DB/cache are up — it stays
green even when that worker has died, so runs silently stall.

``analysis_worker_health`` closes that gap: it pings the worker over the broker
and reports whether at least one is consuming. A dead worker OR an unreachable
broker both surface as ``ok=False`` — either way, no analysis can make progress.
"""

from __future__ import annotations

from typing import TypedDict

# A broadcast ping waits up to this long for any worker to reply before we treat
# the pool as empty. Kept short so a health probe never blocks a request for long.
_PING_TIMEOUT_SECONDS = 1.5


class WorkerHealth(TypedDict):
    ok: bool
    detail: str
    workers: int


def analysis_worker_health(timeout: float = _PING_TIMEOUT_SECONDS) -> WorkerHealth:
    """Whether the analysis worker is alive and consuming.

    Returns ``ok=True`` when at least one worker replies to a broadcast ping, or
    when the app is in eager mode (local dev / tests run the pipeline in-process,
    so there is no separate worker to be down). ``ok=False`` when the broker is
    unreachable or no worker answers within ``timeout``.
    """
    from config.celery_rabbit import analysis_app

    if analysis_app.conf.task_always_eager:
        return {"ok": True, "detail": "eager (in-process, no broker)", "workers": 0}

    try:
        replies = analysis_app.control.ping(timeout=timeout) or []
    except Exception as exc:  # broker down / connection refused / timeout
        return {"ok": False, "detail": f"broker unreachable: {exc}", "workers": 0}

    workers = len(replies)
    if workers == 0:
        return {"ok": False, "detail": "no analysis worker responded to ping", "workers": 0}
    return {"ok": True, "detail": "consuming", "workers": workers}
