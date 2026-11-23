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


def queue_send(action_id: int) -> None:
    """Send one approved action on the send queue. Called from a brand tick (scheduler
    or worker, never a request), so without a broker it runs inline, like the tick."""
    if analysis_app.conf.task_always_eager:
        _dispatch_action(action_id)
    else:
        dispatch_action_task.delay(action_id)


def queue_prepare(task_id: int) -> None:
    """Draft one due task on the background queue. Called from a brand tick, so without
    a broker it runs inline, like the tick."""
    if analysis_app.conf.task_always_eager:
        _run(task_id)
    else:
        run_growth_task.delay(task_id)


def _check_indexing(integration_id: int) -> None:
    # The integrations task opens and closes its own DB connections.
    from apps.integrations.tasks import run_gsc_index_sync

    run_gsc_index_sync(integration_id)


@analysis_app.task(name="growth_agent.check_indexing")
def check_indexing_task(integration_id: int) -> None:
    _check_indexing(integration_id)


def dispatch_indexing_check(integration_id: int) -> None:
    """Inspect every sitemap page with Search Console (slow: ~8s a page), off the request path."""
    if analysis_app.conf.task_always_eager:
        threading.Thread(target=_check_indexing, args=(integration_id,), daemon=True).start()
    else:
        check_indexing_task.delay(integration_id)


def _send_to_bing(program_id: int) -> None:
    from django.db import close_old_connections

    from .models import GrowthProgram
    from .services.indexing import bing

    close_old_connections()
    try:
        program = GrowthProgram.objects.select_related("organization").filter(pk=program_id).first()
        if program is not None:
            bing.send(program)
    finally:
        close_old_connections()


@analysis_app.task(name="growth_agent.send_to_bing")
def send_to_bing_task(program_id: int) -> None:
    _send_to_bing(program_id)


def dispatch_bing_send(program_id: int) -> None:
    """Read the sitemap and send new and changed pages to Bing (IndexNow), off the request path."""
    if analysis_app.conf.task_always_eager:
        threading.Thread(target=_send_to_bing, args=(program_id,), daemon=True).start()
    else:
        send_to_bing_task.delay(program_id)


def _rank_satellite(program_id: int) -> None:
    from django.db import close_old_connections

    from .models import GrowthProgram
    from .services.indexing import satellite_rank

    close_old_connections()
    try:
        program = GrowthProgram.objects.filter(pk=program_id).first()
        if program is not None:
            satellite_rank.run_checks(program)
    finally:
        close_old_connections()


@analysis_app.task(name="growth_agent.rank_satellite_articles")
def rank_satellite_task(program_id: int) -> None:
    _rank_satellite(program_id)


def dispatch_satellite_rank(program_id: int) -> None:
    """Check satellite articles in signalor.ai's Search Console (slow), off the request path."""
    if analysis_app.conf.task_always_eager:
        threading.Thread(target=_rank_satellite, args=(program_id,), daemon=True).start()
    else:
        rank_satellite_task.delay(program_id)


def _check_social(program_id: int) -> None:
    from django.db import close_old_connections

    from .models import GrowthProgram
    from .services.social import check as social_check

    close_old_connections()
    try:
        program = GrowthProgram.objects.select_related("organization").filter(pk=program_id).first()
        if program is not None:
            social_check.run_check(program)
    finally:
        close_old_connections()


@analysis_app.task(name="growth_agent.check_social")
def check_social_task(program_id: int) -> None:
    _check_social(program_id)


def dispatch_social_check(program_id: int) -> None:
    """Score the brand's social presence (YouTube and Reddit calls), off the request path."""
    if analysis_app.conf.task_always_eager:
        threading.Thread(target=_check_social, args=(program_id,), daemon=True).start()
    else:
        check_social_task.delay(program_id)


def _find_guest_sites(program_id: int) -> None:
    from django.db import close_old_connections

    from .models import GrowthProgram
    from .services.guest_posts import finder

    close_old_connections()
    try:
        program = GrowthProgram.objects.select_related("organization").filter(pk=program_id).first()
        if program is not None:
            finder.run_find(program)
    finally:
        close_old_connections()


@analysis_app.task(name="growth_agent.find_guest_sites")
def find_guest_sites_task(program_id: int) -> None:
    _find_guest_sites(program_id)


def dispatch_guest_site_find(program_id: int) -> None:
    """Search for sites that accept guest articles (Serper and page fetches), off the request path."""
    if analysis_app.conf.task_always_eager:
        threading.Thread(target=_find_guest_sites, args=(program_id,), daemon=True).start()
    else:
        find_guest_sites_task.delay(program_id)


def _refresh_keywords(program_id: int) -> None:
    from django.db import close_old_connections

    from .models import GrowthProgram
    from .services.keywords import refresh

    close_old_connections()
    try:
        program = GrowthProgram.objects.select_related("organization").filter(pk=program_id).first()
        if program is not None:
            refresh.run_refresh(program)
    finally:
        close_old_connections()


@analysis_app.task(name="growth_agent.refresh_keywords")
def refresh_keywords_task(program_id: int) -> None:
    _refresh_keywords(program_id)


def dispatch_keyword_refresh(program_id: int) -> None:
    """Read the brand's keywords and competitor gaps (paid DataForSEO calls), off the request path."""
    if analysis_app.conf.task_always_eager:
        threading.Thread(target=_refresh_keywords, args=(program_id,), daemon=True).start()
    else:
        refresh_keywords_task.delay(program_id)


def _check_site(program_id: int) -> None:
    from django.db import close_old_connections

    from .models import GrowthProgram
    from .services.site_watch import watch

    close_old_connections()
    try:
        program = GrowthProgram.objects.select_related("organization").filter(pk=program_id).first()
        if program is not None:
            watch.run_check(program)
    finally:
        close_old_connections()


@analysis_app.task(name="growth_agent.check_site")
def check_site_task(program_id: int) -> None:
    _check_site(program_id)


def dispatch_site_check(program_id: int) -> None:
    """Check the brand's site for problems (homepage, robots.txt, sitemap), off the request path."""
    if analysis_app.conf.task_always_eager:
        threading.Thread(target=_check_site, args=(program_id,), daemon=True).start()
    else:
        check_site_task.delay(program_id)


def _check_ranks(program_id: int) -> None:
    from django.db import close_old_connections

    from .models import GrowthProgram
    from .services.rank_tracking import check

    close_old_connections()
    try:
        program = GrowthProgram.objects.select_related("organization").filter(pk=program_id).first()
        if program is not None:
            check.run_check(program)
    finally:
        close_old_connections()


@analysis_app.task(name="growth_agent.check_ranks")
def check_ranks_task(program_id: int) -> None:
    _check_ranks(program_id)


def dispatch_rank_check(program_id: int) -> None:
    """Weekly SEO positions (Serper) and AI Overview citations (DataForSEO), off the request path."""
    if analysis_app.conf.task_always_eager:
        threading.Thread(target=_check_ranks, args=(program_id,), daemon=True).start()
    else:
        check_ranks_task.delay(program_id)


def _run_tick(program_id: int) -> None:
    import logging

    from .services.scheduling import tick

    try:
        tick.run(program_id)
    except Exception:  # noqa: BLE001 - one brand's failure never stops another's tick
        logging.getLogger("apps").exception("growth tick failed for program %s", program_id)


@analysis_app.task(name="growth_agent.brand_tick")
def brand_tick_task(program_id: int) -> None:
    from django.db import close_old_connections

    close_old_connections()
    try:
        _run_tick(program_id)
    finally:
        close_old_connections()


def dispatch_brand_tick(program_id: int) -> None:
    """One brand's round of work. On the background queue; inline without a broker
    (the scheduler is its own process, so blocking it is fine there)."""
    if analysis_app.conf.task_always_eager:
        _run_tick(program_id)
    else:
        brand_tick_task.delay(program_id)
