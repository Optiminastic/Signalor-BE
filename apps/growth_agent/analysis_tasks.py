"""
Growth Agent background tasks on the RabbitMQ Celery app.

Named ``analysis_tasks`` so ``config.celery_rabbit`` autodiscovers it. Without a
broker (local dev, tests) the app is eager, which would block the request, so
``dispatch_positioning_report`` falls back to a daemon thread, matching
``apps.analyzer.tasks.start_analysis_task``.
"""

from __future__ import annotations

import threading

from config.celery_rabbit import analysis_app


def _build(report_id: int) -> None:
    from django.db import close_old_connections

    from .models import PositioningReport
    from .services.autopilot import advance_onboarding
    from .services.positioning.report import build_report

    close_old_connections()
    try:
        build_report(report_id)
        # A finished report lets an onboarding program create its plan right away.
        advance_onboarding(PositioningReport.objects.select_related("program").get(pk=report_id).program)
    finally:
        close_old_connections()


@analysis_app.task(name="growth_agent.build_positioning_report")
def build_positioning_report_task(report_id: int) -> None:
    _build(report_id)


def dispatch_positioning_report(report_id: int) -> None:
    if analysis_app.conf.task_always_eager:
        threading.Thread(target=_build, args=(report_id,), daemon=True).start()
    else:
        build_positioning_report_task.delay(report_id)


def _track(snapshot_id: int) -> None:
    from django.db import close_old_connections

    from .services.tracking.tracker import run_snapshot

    close_old_connections()
    try:
        run_snapshot(snapshot_id)
    finally:
        close_old_connections()


@analysis_app.task(name="growth_agent.run_weekly_snapshot")
def run_weekly_snapshot_task(snapshot_id: int) -> None:
    _track(snapshot_id)


def dispatch_weekly_snapshot(snapshot_id: int) -> None:
    if analysis_app.conf.task_always_eager:
        threading.Thread(target=_track, args=(snapshot_id,), daemon=True).start()
    else:
        run_weekly_snapshot_task.delay(snapshot_id)


@analysis_app.task(name="growth_agent.run_task")
def run_growth_task(task_id: int) -> None:
    from django.db import close_old_connections

    from .services.execution.executor import run_task

    close_old_connections()
    try:
        run_task(task_id)
    finally:
        close_old_connections()


def _chat(reply_id: int) -> None:
    from django.db import close_old_connections

    from .services.chat.agent import run_turn

    close_old_connections()
    try:
        run_turn(reply_id)
    finally:
        close_old_connections()


@analysis_app.task(name="growth_agent.run_chat_turn")
def run_chat_turn_task(reply_id: int) -> None:
    _chat(reply_id)


def dispatch_chat_turn(reply_id: int) -> None:
    if analysis_app.conf.task_always_eager:
        threading.Thread(target=_chat, args=(reply_id,), daemon=True).start()
    else:
        run_chat_turn_task.delay(reply_id)


def _preview(task_id: int) -> None:
    from django.db import close_old_connections

    from .services.execution.on_demand import run_preview

    close_old_connections()
    try:
        run_preview(task_id)
    finally:
        close_old_connections()


@analysis_app.task(name="growth_agent.preview_task")
def preview_task(task_id: int) -> None:
    _preview(task_id)


def dispatch_preview(task_id: int) -> None:
    if analysis_app.conf.task_always_eager:
        threading.Thread(target=_preview, args=(task_id,), daemon=True).start()
    else:
        preview_task.delay(task_id)


def _run(task_id: int) -> None:
    from django.db import close_old_connections

    from .services.execution.executor import run_task

    close_old_connections()
    try:
        run_task(task_id)
    finally:
        close_old_connections()


def dispatch_task_run(task_id: int) -> None:
    if analysis_app.conf.task_always_eager:
        threading.Thread(target=_run, args=(task_id,), daemon=True).start()
    else:
        run_growth_task.delay(task_id)


def _dispatch_action(action_id: int) -> None:
    from django.db import close_old_connections

    from .services.control.dispatch import dispatch

    close_old_connections()
    try:
        dispatch(action_id)
    finally:
        close_old_connections()


@analysis_app.task(name="growth_agent.dispatch_action")
def dispatch_action_task(action_id: int) -> None:
    """Send one approved action. Safe to deliver twice: the second finds nothing to claim."""
    _dispatch_action(action_id)
