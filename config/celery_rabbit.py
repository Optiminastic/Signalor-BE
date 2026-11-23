"""Second Celery app — RabbitMQ-backed, dedicated to analysis runs.

The primary Celery app (``config.celery:app``) uses Redis as its broker and
runs the sitemap audit. A Celery app can only have ONE broker, so to put the
heavy analyze / re-analyze pipeline on RabbitMQ without disturbing the existing
Redis-backed sitemap task, we run a SECOND app here bound to RabbitMQ.

Invoked by its own worker (``-Q analysis`` so it consumes the work queue but
NOT the dead-letter queue ``analysis.dlq``)::

    celery -A config.celery_rabbit worker -Q analysis --loglevel=info --concurrency=2

Tasks register on this app via ``workers.analysis.tasks`` (note: a different
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

# The broker MUST be a constructor argument, not a later `conf.broker_url =`
# assignment. `autodiscover_tasks()` below triggers Django setup, which imports
# config/celery.py and runs `config_from_object("django.conf:settings")` with
# CELERY_BROKER_URL pointing at Redis - that clobbers a plain assignment and the
# worker silently ends up on the wrong broker. Constructor args land in the
# app's `_preconf`, which outranks any config source loaded afterwards.
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "")
analysis_app = Celery("signalor_analysis", broker=RABBITMQ_URL)
# Celery reads the CELERY_BROKER_URL environment variable BEFORE the constructor
# argument (celery.app.utils.Settings.broker_url), and the shared stack.env sets it
# to Redis for config.celery's app. That silently moved this whole app onto Redis
# in production: no quorum queues, no delivery limit, no dead-letter queues.
# The read and write URLs are consulted before broker_url and are only overridden
# by CELERY_BROKER_READ_URL / CELERY_BROKER_WRITE_URL, which nothing sets, so
# pinning them keeps both publishers (web) and workers on RabbitMQ.
if RABBITMQ_URL:
    analysis_app.conf.broker_read_url = RABBITMQ_URL
    analysis_app.conf.broker_write_url = RABBITMQ_URL

# `celery -A config.celery_rabbit` resolves the app by looking for `.app`, then
# `.celery`, then by scanning the module. Without this alias that search can
# bind config.celery's Redis-backed app instead, so the worker silently consumes
# the wrong broker while the web tier publishes to RabbitMQ - no error anywhere,
# tasks simply never arrive. Name it explicitly.
app = analysis_app

# Configured directly (not via the shared ``CELERY_*`` settings namespace) so
# the existing Redis-Celery config in config/settings/base.py is left untouched.
# Eager only without RabbitMQ (local dev, tests). Not broker_url: that reports the Redis env var.
analysis_app.conf.task_always_eager = not RABBITMQ_URL
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
_growth_dlx = Exchange("growth.dlx", type="direct")
# The Growth Agent's own queues, each served by its own worker service, so chat
# replies and approved sends never wait behind a 40-minute site analysis.
GROWTH_INTERACTIVE = "growth.interactive"
GROWTH_BACKGROUND = "growth.background"


def _growth_queue(name: str) -> Queue:
    """Same durability as ``analysis``: quorum, parked after 3 failed deliveries."""
    return Queue(
        name,
        Exchange(name, type="direct"),
        routing_key=name,
        queue_arguments={
            "x-queue-type": "quorum",
            "x-delivery-limit": 3,
            "x-dead-letter-exchange": "growth.dlx",
            "x-dead-letter-routing-key": "growth.dead",
        },
    )


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
    *(_growth_queue(name) for name in (GROWTH_INTERACTIVE, GROWTH_BACKGROUND)),
    Queue("growth.dlq", _growth_dlx, routing_key="growth.dead"),
]
analysis_app.conf.task_routes = {
    "analyzer.run_analysis": {"queue": "analysis"},
    "analyzer.run_scheduled_analysis": {"queue": "analysis"},
    "analyzer.run_outreach_benchmark": {"queue": "analysis"},
    # Growth Agent: someone is waiting on these, so they never queue behind analyses.
    "growth_agent.run_chat_turn": {"queue": GROWTH_INTERACTIVE},
    "growth_agent.preview_task": {"queue": GROWTH_INTERACTIVE},
    # Approved sends take seconds and the owner is waiting, so they never wait behind
    # drafts on the background queue. (Not a queue of their own: production's compose
    # lives in Coolify, and a queue no worker consumes would strand every send.)
    "growth_agent.dispatch_action": {"queue": GROWTH_INTERACTIVE},
    # Everything else the agent does (ticks, drafts, sends, weekly checks).
    "growth_agent.*": {"queue": GROWTH_BACKGROUND},
}

# Per-task time limits (seconds): a stuck job frees its slot quickly instead of
# holding it for the 40 minutes an analysis may need. (hard, soft)
GROWTH_TIME_LIMITS = {
    "growth_agent.run_chat_turn": (120, 110),
    "growth_agent.preview_task": (300, 280),
    "growth_agent.run_task": (300, 280),
    "growth_agent.dispatch_action": (120, 110),
    "growth_agent.brand_tick": (20 * 60, 19 * 60),
    "growth_agent.run_weekly_snapshot": (20 * 60, 19 * 60),
    "growth_agent.build_positioning_report": (20 * 60, 19 * 60),
    # Checks up to 60 candidate pages, each with a 10 s fetch limit.
    "growth_agent.find_guest_sites": (15 * 60, 14 * 60),
}
# Other growth checks (ranks, social, keywords, site, indexing, guest sites): 10 minutes.
GROWTH_DEFAULT_TIME_LIMIT = (600, 570)


class _GrowthTimeLimits:
    """Celery annotation object: time limits for every ``growth_agent.*`` task."""

    def annotate(self, task):
        if not task.name.startswith("growth_agent."):
            return None
        hard, soft = GROWTH_TIME_LIMITS.get(task.name, GROWTH_DEFAULT_TIME_LIMIT)
        return {"time_limit": hard, "soft_time_limit": soft}


analysis_app.conf.task_annotations = [_GrowthTimeLimits()]

# Look for `analysis_tasks` modules (NOT `celery_tasks`) so only the analysis
# task registers on this RabbitMQ app.
analysis_app.autodiscover_tasks(related_name="analysis_tasks")
# See config/celery.py: workers/ is outside apps/, so it needs explicit imports.
analysis_app.conf.imports = tuple(analysis_app.conf.imports or ()) + (
    "workers.analysis.tasks",
    "workers.crawling.tasks",
)


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
