"""Second Celery app — RabbitMQ-backed, dedicated to analysis runs.

The primary Celery app (``config.celery:app``) uses Redis as its broker and
runs the sitemap audit. A Celery app can only have ONE broker, so to put the
heavy analyze / re-analyze pipeline on RabbitMQ without disturbing the existing
Redis-backed sitemap task, we run a SECOND app here bound to RabbitMQ.

Invoked by its own worker (``-Q analysis`` so it consumes the work queue but
NOT the dead-letter queue ``analysis.dlq``)::

    celery -A config.celery_rabbit worker -Q analysis --loglevel=info --concurrency=2

Tasks register on this app via ``apps.<x>.analysis_tasks`` (note: a different
``related_name`` than the Redis app's ``celery_tasks``) so the analysis task
lands on RabbitMQ and the sitemap task stays on Redis.

When ``RABBITMQ_URL`` is unset (local dev / tests) the app runs eagerly; the
analysis dispatcher (``apps.analyzer.tasks.start_analysis_task``) checks that
flag and falls back to a thread so ``/analyze/`` stays fast without a broker.
"""

from __future__ import annotations

import logging
import os

from celery import Celery
from celery.signals import worker_ready
from kombu import Exchange, Queue

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

analysis_app = Celery("signalor_analysis")

# Configured directly (not via the shared ``CELERY_*`` settings namespace) so
# the existing Redis-Celery config in config/settings/base.py is left untouched.
analysis_app.conf.broker_url = os.getenv("RABBITMQ_URL", "")  # amqp://user:pass@host:5672//
analysis_app.conf.task_always_eager = not bool(analysis_app.conf.broker_url)
analysis_app.conf.task_eager_propagates = True
analysis_app.conf.task_acks_late = True
analysis_app.conf.worker_prefetch_multiplier = 1
analysis_app.conf.task_time_limit = 60 * 40  # analysis can run longer than a sitemap audit
analysis_app.conf.task_soft_time_limit = 60 * 35
analysis_app.conf.accept_content = ["json"]
analysis_app.conf.task_serializer = "json"
analysis_app.conf.result_serializer = "json"
analysis_app.conf.timezone = "UTC"

# ── Dead-letter wiring ─────────────────────────────────────────────────────
# The main work queue is a QUORUM queue with a redelivery limit. A "poison" job
# (one that crashes the worker on every delivery, before it can ack) would
# otherwise be redelivered forever under task_acks_late. After x-delivery-limit
# redeliveries, RabbitMQ routes the message to the dead-letter exchange →
# `analysis.dlq`, where it's parked for manual inspection instead of looping.
#
# Normal failures never reach here: run_analysis_task catches its exceptions,
# marks the run FAILED in the DB, and acks the message cleanly. The DLQ is only
# the safety net for messages that can't be processed at all.
_dlx = Exchange("analysis.dlx", type="direct")
analysis_app.conf.task_default_queue = "analysis"
analysis_app.conf.task_queues = [
    Queue(
        "analysis",
        Exchange("analysis", type="direct"),
        routing_key="analysis",
        queue_arguments={
            "x-queue-type": "quorum",
            "x-delivery-limit": 3,  # park the message after 3 failed deliveries
            "x-dead-letter-exchange": "analysis.dlx",
            "x-dead-letter-routing-key": "analysis.dead",
        },
    ),
    Queue("analysis.dlq", _dlx, routing_key="analysis.dead"),  # the "problem shelf"
]
analysis_app.conf.task_routes = {"analyzer.run_analysis": {"queue": "analysis"}}

# Look for `analysis_tasks` modules (NOT `celery_tasks`) so only the analysis
# task registers on this RabbitMQ app.
analysis_app.autodiscover_tasks(related_name="analysis_tasks")


@worker_ready.connect
def _ensure_dlq_topology(sender=None, **_):
    """Declare the analysis queue + dead-letter queue/exchange/binding on boot.

    The worker is started with ``-Q analysis`` so it only *consumes* the main
    queue — it would never declare ``analysis.dlq`` on its own. If the DLQ and
    its binding to ``analysis.dlx`` don't exist, RabbitMQ silently drops any
    dead-lettered message. Declaring here (idempotent) guarantees the problem
    shelf is in place before the first poison job can be parked.
    """
    if analysis_app.conf.task_always_eager:
        return
    try:
        with analysis_app.connection_for_write() as conn:
            channel = conn.default_channel
            for queue in analysis_app.conf.task_queues:
                queue(channel).declare()
    except Exception:  # best-effort; don't crash the worker over declaration
        logging.getLogger("apps").exception("failed to declare analysis DLQ topology")
