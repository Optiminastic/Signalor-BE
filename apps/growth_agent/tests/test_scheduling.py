"""
Running many brands: one tick per active brand per round, a fair share per
brand, isolation between brands, one tick per brand at a time, separate queues,
the active-brand cap and heartbeat alerts.

Run: python manage.py test apps.growth_agent.tests.test_scheduling --settings=config.settings.test
"""

from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.organizations.models import Organization
from config.celery_rabbit import GROWTH_BACKGROUND, GROWTH_INTERACTIVE, analysis_app

from ..models import GrowthAction, GrowthCycle, GrowthProgram, GrowthTask, PositioningReport
from ..services import autopilot
from ..services.chat import write_tools
from ..services.planning import plan
from ..services.scheduling import capacity, health, tick
from ..services.scheduling import round as rounds
from .test_autopilot import _report
from .test_execution_flow import REGISTRY, FakeChannel

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "scheduling"}}
BRANDS = 50


def brand(number: int, status=GrowthProgram.Status.ACTIVE) -> GrowthProgram:
    owner = f"owner{number}@b{number}.example"
    org = Organization.objects.create(name=f"B{number}", url=f"https://b{number}.example", owner_email=owner)
    program = GrowthProgram.objects.create(organization=org, created_by_email=owner, status=status)
    today = timezone.now().date()
    GrowthCycle.objects.create(program=program, starts_on=today, ends_on=today + timedelta(days=27))
    return program


def due_task(program: GrowthProgram, title="Page") -> GrowthTask:
    return GrowthTask.objects.create(
        cycle=program.cycles.get(),
        week=1,
        scheduled_for=timezone.now().date(),
        channel="owned_content",
        tactic="answer_content",
        title=title,
        target="x",
    )


@override_settings(CACHES=LOCMEM)
class SchedulingTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.channel = FakeChannel()
        for patcher in (
            mock.patch.dict(REGISTRY, {"owned_content": self.channel}),
            # The weekly and daily checks call external services; they have their own tests.
            mock.patch.object(tick, "_periodic_steps", return_value=[]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)


class RoundTests(SchedulingTestCase):
    def test_every_one_of_50_brands_gets_a_tick_in_one_round(self):
        programs = [brand(n) for n in range(BRANDS)]
        result = rounds.run_round()
        self.assertEqual(result.ticks, BRANDS)
        stamped = GrowthProgram.objects.filter(pk__in=[p.pk for p in programs], last_tick_at__isnull=False)
        self.assertEqual(stamped.count(), BRANDS)

    def test_paused_stopped_and_killed_brands_get_no_tick(self):
        brand(1, GrowthProgram.Status.PAUSED)
        killed = brand(2)
        GrowthProgram.objects.filter(pk=killed.pk).update(kill_switch=True)
        self.assertEqual(rounds.run_round().ticks, 0)

    def test_a_round_already_running_makes_the_next_one_skip(self):
        cache.add(rounds.ROUND_LOCK_KEY, "x", 60)
        self.assertTrue(rounds.run_round().skipped)

    def test_the_round_writes_the_scheduler_heartbeat(self):
        rounds.run_round()
        self.assertIsNotNone(cache.get(rounds.HEARTBEAT_KEY))


class TickTests(SchedulingTestCase):
    def test_a_brand_with_a_backlog_gets_only_its_fair_share(self):
        busy, quiet = brand(1), brand(2)
        for n in range(3 * tick.PREPARES_PER_TICK):
            due_task(busy, f"Busy {n}")
        due_task(quiet, "Quiet")
        rounds.run_round()
        self.assertEqual(
            GrowthAction.objects.filter(task__cycle__program=busy).count(), tick.PREPARES_PER_TICK
        )
        self.assertEqual(GrowthAction.objects.filter(task__cycle__program=quiet).count(), 1)
        rounds.run_round()
        self.assertEqual(
            GrowthAction.objects.filter(task__cycle__program=busy).count(), 2 * tick.PREPARES_PER_TICK
        )

    def test_a_brand_whose_tick_crashes_does_not_stop_the_others(self):
        programs = [brand(n) for n in range(5)]
        broken = programs[2]
        real = tick._work

        def work(program, result):
            if program.pk == broken.pk:
                raise RuntimeError("hung connector")
            return real(program, result)

        with mock.patch.object(tick, "_work", side_effect=work):
            rounds.run_round()
        ticked = set(GrowthProgram.objects.filter(last_tick_at__isnull=False).values_list("pk", flat=True))
        self.assertEqual(ticked, {p.pk for p in programs} - {broken.pk})

    def test_a_failing_step_does_not_stop_the_brands_next_steps(self):
        program = brand(1)
        calls = []
        steps = [("bad", lambda: 1 / 0), ("good", lambda: calls.append("good"))]
        with mock.patch.object(tick, "_periodic_steps", return_value=steps):
            result = tick.run(program.pk)
        self.assertEqual((result.failed_steps, calls), (["bad"], ["good"]))

    def test_an_overlapping_tick_for_the_same_brand_skips(self):
        program = brand(1)
        due_task(program)
        cache.add(tick._lock_key(program.pk), "other-worker", 60)
        self.assertIsNone(tick.run(program.pk))
        self.assertFalse(GrowthAction.objects.exists())
        cache.delete(tick._lock_key(program.pk))
        self.assertIsNotNone(tick.run(program.pk))

    def test_the_lock_is_released_after_a_tick(self):
        program = brand(1)
        tick.run(program.pk)
        self.assertIsNone(cache.get(tick._lock_key(program.pk)))


class QueueTests(TestCase):
    def route(self, name):
        return analysis_app.amqp.router.route({}, name)["queue"].name

    def test_chat_and_previews_never_share_a_queue_with_analyses(self):
        self.assertEqual(self.route("growth_agent.run_chat_turn"), GROWTH_INTERACTIVE)
        self.assertEqual(self.route("growth_agent.preview_task"), GROWTH_INTERACTIVE)
        for name in ("growth_agent.brand_tick", "growth_agent.dispatch_action", "growth_agent.run_task"):
            self.assertEqual(self.route(name), GROWTH_BACKGROUND)
        self.assertEqual(self.route("analyzer.run_analysis"), "analysis")

    def test_growth_tasks_have_short_time_limits(self):
        import apps.growth_agent.analysis_tasks  # noqa: F401  (registers the tasks)

        analysis_app.finalize()
        self.assertEqual(analysis_app.tasks["growth_agent.dispatch_action"].time_limit, 120)
        self.assertEqual(analysis_app.tasks["growth_agent.check_ranks"].time_limit, 600)


@override_settings(GROWTH_MAX_ACTIVE_PROGRAMS=2)
class CapacityTests(SchedulingTestCase):
    def test_a_brand_beyond_the_cap_stays_onboarding(self):
        brand(1), brand(2)
        waiting = brand(3, GrowthProgram.Status.ONBOARDING)
        GrowthCycle.objects.filter(program=waiting).delete()
        PositioningReport.objects.create(
            program=waiting, status=PositioningReport.Status.COMPLETE, **_report()
        )
        with self.assertRaises(capacity.CapacityReached):
            plan.create_plan(waiting)
        autopilot.advance_onboarding(waiting)  # the scheduler's path: logged, not raised
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, GrowthProgram.Status.ONBOARDING)
        self.assertFalse(waiting.cycles.exists())  # the plan rolled back

    def test_resuming_beyond_the_cap_is_refused(self):
        brand(1), brand(2)
        paused = brand(3, GrowthProgram.Status.PAUSED)
        self.assertIn("error", write_tools.resume_program(paused, {}))
        paused.refresh_from_db()
        self.assertEqual(paused.status, GrowthProgram.Status.PAUSED)

    def test_resuming_within_the_cap_works(self):
        brand(1)
        paused = brand(2, GrowthProgram.Status.PAUSED)
        self.assertEqual(write_tools.resume_program(paused, {}), {"program_status": "active"})


@override_settings(GROWTH_OPS_ALERT_EMAILS=["ops@signalor.example"])
class HealthTests(SchedulingTestCase):
    def test_a_healthy_round_has_no_problems(self):
        brand(1)
        rounds.run_round()
        self.assertEqual(health.problems(), [])

    def test_no_scheduler_round_is_a_problem(self):
        self.assertEqual([p.key for p in health.problems()], ["scheduler"])

    def test_a_brand_without_a_recent_tick_is_a_problem(self):
        program = brand(1)
        rounds.run_round()
        GrowthProgram.objects.filter(pk=program.pk).update(last_tick_at=timezone.now() - timedelta(hours=1))
        self.assertEqual([p.key for p in health.problems()], [f"brand:{program.pk}"])

    def test_alerts_are_emailed_once_an_hour_per_problem(self):
        found = health.problems()
        self.assertEqual(health.alert(found), 1)
        self.assertEqual(health.alert(found), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ops@signalor.example"])


class DraftAheadTests(SchedulingTestCase):
    def test_tasks_due_within_ten_days_are_drafted_now_but_later_ones_wait(self):
        program = brand(1)
        soon = due_task(program, "Soon")
        GrowthTask.objects.filter(pk=soon.pk).update(scheduled_for=timezone.now().date() + timedelta(days=5))
        later = due_task(program, "Later")
        GrowthTask.objects.filter(pk=later.pk).update(
            scheduled_for=timezone.now().date() + timedelta(days=20)
        )
        rounds.run_round()
        self.assertTrue(GrowthAction.objects.filter(task=soon).exists())
        self.assertFalse(GrowthAction.objects.filter(task=later).exists())

    def test_an_early_draft_is_not_sent_before_its_day_even_if_approved(self):
        from ..services.control import dispatch

        program = brand(1)
        task = due_task(program, "Next week")
        GrowthTask.objects.filter(pk=task.pk).update(scheduled_for=timezone.now().date() + timedelta(days=5))
        rounds.run_round()
        action = GrowthAction.objects.get(task=task)
        self.assertFalse(dispatch.dispatchable(timezone.now().date()).filter(pk=action.pk).exists())
