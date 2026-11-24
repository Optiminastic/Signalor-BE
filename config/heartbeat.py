"""Push-based liveness heartbeat for the Celery workers.

Each Celery worker (Redis-backed sitemap worker in ``config.celery`` and the
RabbitMQ-backed analysis worker in ``config.celery_rabbit``) has no HTTP surface
for an uptime monitor to poll. So instead of being *polled*, each worker *pings*
a Better Stack Uptime "heartbeat" monitor on a fixed interval while it is alive.
When the worker crashes / OOMs / is redeployed / loses its broker, the pings
stop and Better Stack alerts after its configured grace period.

This mirrors the drip scheduler's heartbeat (``apps/drip/scheduler.py``): a
periodic write on an interval, failures swallowed and logged so the heartbeat can
never take the worker down. The only difference is the "write" is an HTTP GET to
the monitor URL rather than a cache write.

Wiring: ``register_worker_heartbeat(app, url, label=...)`` connects Celery's
``worker_ready`` / ``worker_shutdown`` signals for the given app. Multiple
``worker_ready`` receivers are allowed, so this composes with the existing
``_ensure_dlq_topology`` hook in ``config/celery_rabbit.py``.

Liveness semantics: the timer thread proves the worker *process* is alive (the
common failure modes — crash, OOM, restart). It does not by itself prove the
broker → consume → execute path; a Beat-driven ping would be needed for that.
"""

from __future__ import annotations

import logging
import os
import threading

import requests

logger = logging.getLogger("apps")

DEFAULT_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 15
PING_TIMEOUT_SECONDS = 5


def _interval_seconds() -> int:
    """Ping cadence from env, floored so a typo can't hammer the monitor."""
    raw = os.getenv("BETTERSTACK_HEARTBEAT_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS))
    try:
        return max(MIN_INTERVAL_SECONDS, int(raw))
    except ValueError:
        logger.warning(
            "Invalid BETTERSTACK_HEARTBEAT_INTERVAL_SECONDS=%r, falling back to %s",
            raw,
            DEFAULT_INTERVAL_SECONDS,
        )
        return DEFAULT_INTERVAL_SECONDS


def _ping(url: str, label: str) -> None:
    """GET the heartbeat URL. Never raises — a monitoring blip must not disturb
    the worker (matches the drip heartbeat's swallow-and-log stance)."""
    try:
        requests.get(url, timeout=PING_TIMEOUT_SECONDS)
    except Exception:
        logger.exception("heartbeat ping failed for %s", label)


def register_worker_heartbeat(celery_app, heartbeat_url: str, *, label: str) -> None:
    """Wire a Better Stack heartbeat for one Celery app.

    ``heartbeat_url`` empty (local dev / eager mode / unset env) → no-op, so the
    worker boots and runs normally without any monitoring config.

    Isolation across the two apps is by env scoping, not signal sender: the
    ``worker_ready`` sender is the consumer (not the app), so filtering by app
    would never fire — matching the existing sender-less hook in
    ``config/celery_rabbit.py``. Each Render worker service sets only its own
    URL, so the other app's registration here is a no-op and never pings.
    """
    from celery.signals import worker_ready, worker_shutdown

    if not heartbeat_url:
        logger.info("heartbeat disabled for %s: no URL", label)
        return

    interval = _interval_seconds()
    stop_event = threading.Event()
    # There is exactly one worker per process, so worker_ready fires once; the
    # flag only guards against a double-connect ever starting the thread twice.
    started = threading.Event()

    def _loop() -> None:
        # Loop until shutdown flips the event. ``wait`` returns True on stop, so
        # a shutdown mid-interval breaks out promptly instead of sleeping it out.
        while not stop_event.wait(interval):
            _ping(heartbeat_url, label)

    thread = threading.Thread(target=_loop, name=f"heartbeat-{label}", daemon=True)

    @worker_ready.connect(weak=False)
    def _start(*_args, **_kwargs) -> None:
        if started.is_set():
            return
        started.set()
        logger.info("heartbeat starting for %s: every %ds", label, interval)
        _ping(heartbeat_url, label)  # ping immediately — worker is up now
        thread.start()

    @worker_shutdown.connect(weak=False)
    def _stop(*_args, **_kwargs) -> None:
        logger.info("heartbeat stopping for %s", label)
        stop_event.set()
