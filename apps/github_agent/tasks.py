"""
Background tasks for the GitHub agent. Same daemon-thread pattern as
apps/integrations/tasks.py (no Celery in v1). Threads close stale DB
connections before doing work so long-lived workers don't reuse dead conns.
"""

import logging
import threading

from django.db import close_old_connections

from .services.orchestrator import open_fix_pr

logger = logging.getLogger("apps")


def start_fix_job(job_id: int):
    """Spawn a daemon thread to generate edits and open the fix PR."""
    thread = threading.Thread(target=_run_fix_job, args=(job_id,), daemon=True)
    thread.start()
    return thread


def _run_fix_job(job_id: int):
    close_old_connections()
    try:
        open_fix_pr(job_id)
    finally:
        close_old_connections()
