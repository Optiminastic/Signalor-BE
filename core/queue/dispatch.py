"""Send work to a queue by task NAME, without importing the task.

``workers/`` sits above the apps: a worker binds an app's service to celery, so
the app must not import the worker back. Four call sites did
(``from workers.analysis.tasks import run_analysis_task``), which is an upward
edge the layering contract rejects.

Dispatching by name removes it. Every task in this codebase already pins an
explicit ``name=`` (``analyzer.run_analysis`` etc.), so ``send_task`` reaches it
through the broker with no Python import at all.

Two brokers, because there are two celery apps:

- ``ANALYSIS`` - the RabbitMQ app (``config.celery_rabbit``) that owns the
  analysis pipeline.
- ``DEFAULT``  - the Redis app (``config.celery``) that owns the sitemap audit.

``is_eager`` is exposed because callers legitimately branch on it: with no broker
configured (local dev, tests) celery runs tasks in-process, and several call
sites deliberately run inline or on a thread instead so an HTTP response is not
blocked by a full crawl. That decision stays with the caller; only the import
moves here.
"""

from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger("apps")

Broker = Literal["analysis", "default"]


def _app(broker: Broker):
    if broker == "analysis":
        from config.celery_rabbit import analysis_app

        return analysis_app
    from config.celery import app

    return app


def is_eager(broker: Broker = "analysis") -> bool:
    """True when this process runs tasks in-process rather than queueing them."""
    try:
        return bool(_app(broker).conf.task_always_eager)
    except Exception:
        logger.warning("could not read task_always_eager for %s", broker, exc_info=True)
        return False


def send(task_name: str, *args, broker: Broker = "analysis", **kwargs) -> bool:
    """Queue ``task_name``. Returns False if it could not be handed to the broker.

    A dispatch failure is reported, never raised: the caller has usually already
    committed the row the task operates on, and losing the request as well turns
    a delayed job into a failed user action.
    """
    try:
        _app(broker).send_task(task_name, args=args, kwargs=kwargs)
        return True
    except Exception:
        logger.exception("failed to queue %s on the %s broker", task_name, broker)
        return False


# Task names, kept beside the dispatcher so a rename is a one-line change here
# rather than a string hunt across the apps.
ANALYSIS_RUN = "analyzer.run_analysis"
ANALYSIS_SCHEDULED = "analyzer.run_scheduled_analysis"
SITEMAP_AUDIT = "analyzer.run_sitemap_audit"
