"""
Weekly tracking: pure measurement/attribution, cost guards, snapshot run, API scoping.

Run: python manage.py test apps.growth_agent.tests.test_tracking --settings=config.settings.test
"""

from datetime import date, timedelta
from unittest import mock

from django.core import mail
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.analyzer.models import AnalysisRun, PromptCitation, PromptResult, PromptTrack

from ..models import GrowthCycle, GrowthProgram, GrowthTask, PositioningReport, WeeklySnapshot
from ..services.positioning.inputs import Citation, EngineAnswer, PositioningInputs
from ..services.tracking import measure, tracker
from .test_api import OTHER_EMAIL, PRO_EMAIL, GrowthApiTestCase

RECHECK = "apps.growth_agent.services.tracking.tracker.recheck_track"
DISPATCH = "apps.growth_agent.views.dispatch_weekly_snapshot"


def _inputs(answers):
    return PositioningInputs(("Signalor",), "signalor.ai", tuple(answers), (), {}, ())


def _answer(engine, brand, domains=()):
    cites = tuple(Citation(d, f"https://{d}/x", "") for d in domains)
    return EngineAnswer(1, "best geo tool", engine, "Signalor" if brand else "Others", brand, cites)


class MeasureTests(SimpleTestCase):
    def setUp(self):
        self.inputs = _inputs(
            [
                _answer("chatgpt", True, ["best-tools.com"]),
                _answer("gemini", False, ["g2.com"]),
                _answer("claude", True, ["blog.example.com"]),
                _answer("perplexity", False),
            ]
        )

    def test_deltas_vs_baseline_and_previous(self):
        result = measure.measure(
            self.inputs,
            baseline_share_of_voice=10.0,
            previous_share_of_voice=40.0,
            baseline_gaps=[],
            done_tasks=[],
        )
        self.assertEqual(result["share_of_voice"], 50.0)
        self.assertEqual(result["delta_vs_baseline"], 40.0)
        self.assertEqual(result["delta_vs_previous"], 10.0)
        self.assertEqual(result["engines"]["chatgpt"], 100.0)

    def test_first_snapshot_has_no_previous_delta(self):
        result = measure.measure(
            self.inputs,
            baseline_share_of_voice=0,
            previous_share_of_voice=None,
            baseline_gaps=[],
            done_tasks=[],
        )
        self.assertIsNone(result["delta_vs_previous"])

    def test_closed_gaps_only_where_brand_is_now_cited(self):
        gaps = [{"domain": "best-tools.com"}, {"domain": "g2.com"}]
        self.assertEqual(
            measure.closed_gaps(gaps, measure.brand_cited_domains(self.inputs)), ["best-tools.com"]
        )

    def test_wins_attribute_done_tasks_whose_target_is_now_cited(self):
        tasks = [
            measure.TaskRef(1, "Get listed", "best-tools.com", "https://best-tools.com/top"),
            measure.TaskRef(2, "Pitch g2", "g2.com", ""),
        ]
        wins = measure.attribute_wins(tasks, measure.brand_cited_domains(self.inputs))
        self.assertEqual([w["task_id"] for w in wins], [1])
        self.assertEqual(wins[0]["claim"], "likely contributed")


# SQLite in-memory test DBs cannot be written from worker threads; re-check sequentially.
@override_settings(GROWTH_RECHECK_WORKERS=1)
class TrackingTestCase(GrowthApiTestCase):
    def setUp(self):
        super().setUp()
        self.program = GrowthProgram.objects.create(
            organization=self.pro_org, created_by_email=PRO_EMAIL, status=GrowthProgram.Status.ACTIVE
        )
        self.run = AnalysisRun.objects.create(
            organization=self.pro_org,
            url=self.pro_org.url,
            brand_name="Signalor",
            email=PRO_EMAIL,
            status=AnalysisRun.Status.COMPLETE,
        )
        self.track = PromptTrack.objects.create(analysis_run=self.run, prompt_text="best geo tool")
        report = PositioningReport.objects.create(
            program=self.program,
            analysis_run=self.run,
            status=PositioningReport.Status.COMPLETE,
            citation_gaps=[{"domain": "best-tools.com"}],
        )
        start = timezone.now().date() - timedelta(days=8)
        self.cycle = GrowthCycle.objects.create(
            program=self.program,
            positioning_report=report,
            starts_on=start,
            ends_on=start + timedelta(days=27),
            baseline_share_of_voice=0.0,
            target_share_of_voice=10.0,
        )

    def fake_recheck(self, track, brand_name, brand_url):
        result = PromptResult.objects.create(
            prompt_track=track, engine="chatgpt", response_text="Signalor leads", brand_mentioned=True
        )
        PromptCitation.objects.create(
            prompt_result=result, url="https://best-tools.com/a", domain="best-tools.com"
        )
        return 1


class TrackerTests(TrackingTestCase):
    def test_elapsed_week_is_clamped(self):
        self.assertEqual(tracker.elapsed_week(self.cycle, self.cycle.starts_on), 0)
        self.assertEqual(tracker.elapsed_week(self.cycle, self.cycle.starts_on + timedelta(days=9)), 1)
        self.assertEqual(tracker.elapsed_week(self.cycle, self.cycle.starts_on + timedelta(days=90)), 4)

    def test_scheduled_snapshot_is_taken_once_per_week(self):
        today = timezone.now().date()
        first = tracker.request_scheduled_snapshot(self.cycle, today)
        WeeklySnapshot.objects.filter(pk=first.pk).update(status=WeeklySnapshot.Status.COMPLETE)
        self.assertEqual(first.week, 1)
        self.assertIsNone(tracker.request_scheduled_snapshot(self.cycle, today))

    def test_not_due_before_first_week_ends(self):
        self.assertIsNone(
            tracker.request_scheduled_snapshot(self.cycle, self.cycle.starts_on + timedelta(days=3))
        )

    def test_manual_check_reuses_pending_and_enforces_cooldown(self):
        snap, needs_run = tracker.request_manual_snapshot(self.cycle)
        self.assertTrue(needs_run)
        again, needs_run = tracker.request_manual_snapshot(self.cycle)
        self.assertEqual((again.pk, needs_run), (snap.pk, False))
        WeeklySnapshot.objects.filter(pk=snap.pk).update(status=WeeklySnapshot.Status.COMPLETE)
        with self.assertRaises(tracker.TrackingError):
            tracker.request_manual_snapshot(self.cycle)

    def test_run_snapshot_measures_only_fresh_answers_and_attributes_wins(self):
        PromptResult.objects.create(
            prompt_track=self.track, engine="gemini", response_text="old", brand_mentioned=False
        )
        GrowthTask.objects.create(
            cycle=self.cycle,
            week=1,
            scheduled_for=date.today(),
            channel="outreach_email",
            tactic="listicle_inclusion",
            title="Get listed",
            target="best-tools.com",
            status=GrowthTask.Status.DONE,
        )
        snap = tracker.request_scheduled_snapshot(self.cycle, timezone.now().date())
        with mock.patch(RECHECK, side_effect=self.fake_recheck):
            tracker.run_snapshot(snap.pk)
        snap.refresh_from_db()
        self.assertEqual(snap.status, WeeklySnapshot.Status.COMPLETE)
        self.assertEqual(snap.answers_checked, 1)  # the old gemini answer is excluded
        self.assertEqual(snap.share_of_voice, 100.0)
        self.assertEqual(snap.closed_gaps, ["best-tools.com"])
        self.assertEqual(snap.wins[0]["title"], "Get listed")
        self.assertEqual(len(mail.outbox), 1)  # scheduled snapshots email the owner

    def test_snapshot_with_no_answers_fails_cleanly(self):
        snap = tracker.request_scheduled_snapshot(self.cycle, timezone.now().date())
        with mock.patch(RECHECK, return_value=0):
            tracker.run_snapshot(snap.pk)
        snap.refresh_from_db()
        self.assertEqual(snap.status, WeeklySnapshot.Status.FAILED)

    def test_final_week_closes_the_cycle(self):
        snap = WeeklySnapshot.objects.create(cycle=self.cycle, week=4)
        with mock.patch(RECHECK, side_effect=self.fake_recheck):
            tracker.run_snapshot(snap.pk)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, GrowthCycle.Status.COMPLETED)


class TrackingApiTests(TrackingTestCase):
    @mock.patch(DISPATCH)
    def test_check_now_then_cooldown(self, dispatch):
        path = f"/api/growth/programs/{self.pro_org.slug}/tracking/"
        self._body = {}
        self.assertEqual(self.signed("POST", path, PRO_EMAIL).status_code, 202)
        WeeklySnapshot.objects.update(status=WeeklySnapshot.Status.COMPLETE)
        self.assertEqual(self.signed("POST", path, PRO_EMAIL).status_code, 429)
        dispatch.assert_called_once()

    def test_get_progress(self):
        response = self.signed("GET", f"/api/growth/programs/{self.pro_org.slug}/tracking/", PRO_EMAIL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["target_share_of_voice"], 10.0)

    def test_cannot_read_someone_elses_progress(self):
        response = self.signed("GET", f"/api/growth/programs/{self.other_org.slug}/tracking/", PRO_EMAIL)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(OTHER_EMAIL, self.other_org.owner_email)
