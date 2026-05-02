"""
Background-thread helpers.

Daemon threads that drive long-running jobs (sitemap audit, schema watch,
rank audit, rank-query refresh, prompt fire-and-save) all need the same
boilerplate:

    1. Open a fresh DB connection (close_old_connections).
    2. Run the work.
    3. On exception, log it AND mark the row as FAILED so the user can see
       a crashed job instead of a silently-stuck "running" row.

This module collapses that pattern into a single helper. Use it like:

    from apps.analyzer._thread_safety import run_in_background_with_status

    run_in_background_with_status(
        model_cls=SitemapAudit,
        instance_id=audit.id,
        status_field="status",
        failure_value=SitemapAudit.Status.FAILED,
        work=lambda: run_sitemap_audit(audit.id),
    )

The helper spawns the daemon thread itself and returns immediately, so the
caller doesn't have to repeat ``threading.Thread(...).start()``.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

logger = logging.getLogger("apps")


def run_in_background_with_status(
    *,
    model_cls: type,
    instance_id: int,
    status_field: str,
    failure_value: Any,
    work: Callable[[], None],
    log_label: str | None = None,
) -> threading.Thread:
    """
    Run ``work`` in a daemon thread; on crash, mark the row as ``failure_value``.

    Args:
        model_cls:     the model class whose row should be flipped to failed.
        instance_id:   primary key of the row.
        status_field:  the status field name (usually "status").
        failure_value: the enum/string to write on crash (e.g. Model.Status.FAILED).
        work:          a zero-arg callable that performs the actual work.
        log_label:     optional human label for log messages (defaults to the
                       model class name).

    Returns the started ``threading.Thread`` so callers can join it in tests.
    """
    label = log_label or model_cls.__name__

    def _runner():
        from django.db import close_old_connections

        try:
            close_old_connections()
            work()
        except Exception:
            logger.exception("%s background work failed for id=%s", label, instance_id)
            try:
                model_cls.objects.filter(pk=instance_id).update(
                    **{status_field: failure_value}
                )
            except Exception:
                logger.exception(
                    "%s failed to mark id=%s as %s", label, instance_id, failure_value,
                )

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    return t


def run_in_background(work: Callable[[], None], *, log_label: str = "background") -> threading.Thread:
    """
    Lightweight version: spawn a daemon thread that just logs failures.

    Use this for jobs without a dedicated DB row to flip (e.g. cache warmup,
    fire-and-forget notifications). For status-tracked jobs, use
    ``run_in_background_with_status`` instead.
    """

    def _runner():
        from django.db import close_old_connections

        try:
            close_old_connections()
            work()
        except Exception:
            logger.exception("%s background work failed", log_label)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    return t
