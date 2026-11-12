"""
Onboarding recovers from a failed analysis, and the agent describes setup truthfully.

A program leaves onboarding only from a completed analysis. If that analysis
failed (a deploy orphaned it, a crash, a timeout), the program used to wait
forever with nothing telling the owner. Now the autopilot restarts it, at most
``MAX_RESTARTS_PER_HOUR`` times an hour, within the owner's normal limits; and
the chat agent gets the real setup stage instead of guessing.

Run: python manage.py test apps.growth_agent.tests.test_onboarding_recovery --settings=config.settings.test
"""

from unittest import mock

from apps.analyzer.models import AnalysisRun

from ..models import GrowthProgram, PositioningReport
from ..services import autopilot
from ..services.chat import agent
from .test_api import PRO_EMAIL, GrowthApiTestCase

START = "apps.growth_agent.services.onboarding.start_analysis_task"
DISPATCH = "apps.growth_agent.services.autopilot.dispatch_positioning_report"
ALLOWED = "apps.growth_agent.services.onboarding._analysis_block_reason"


class OnboardingRecoveryTests(GrowthApiTestCase):
    def setUp(self):
        super().setUp()
        self.program = GrowthProgram.objects.create(organization=self.pro_org, created_by_email=PRO_EMAIL)

    def run_with(self, status: str) -> AnalysisRun:
        return AnalysisRun.objects.create(
            organization=self.pro_org, url=self.pro_org.url, email=PRO_EMAIL, status=status
        )

    def runs(self) -> list[str]:
        return list(
            AnalysisRun.objects.filter(organization=self.pro_org)
            .order_by("id")
            .values_list("status", flat=True)
        )

    @mock.patch(ALLOWED, return_value="")
    @mock.patch(DISPATCH)
    @mock.patch(START)
    def test_a_failed_analysis_is_restarted(self, start, _dispatch, _allowed):
        self.run_with(AnalysisRun.Status.FAILED)
        autopilot.advance_onboarding(self.program)
        start.assert_called_once()
        self.assertEqual(self.runs(), [AnalysisRun.Status.FAILED, AnalysisRun.Status.PENDING])

    @mock.patch(ALLOWED, return_value="")
    @mock.patch(DISPATCH)
    @mock.patch(START)
    def test_a_brand_with_no_analysis_gets_one(self, start, _dispatch, _allowed):
        autopilot.advance_onboarding(self.program)
        start.assert_called_once()

    @mock.patch(ALLOWED, return_value="")
    @mock.patch(DISPATCH)
    @mock.patch(START)
    def test_an_analysis_in_progress_is_left_alone(self, start, _dispatch, _allowed):
        self.run_with(AnalysisRun.Status.SCORING)
        autopilot.advance_onboarding(self.program)
        start.assert_not_called()

    @mock.patch(ALLOWED, return_value="")
    @mock.patch(DISPATCH)
    @mock.patch(START)
    def test_restarts_are_capped_so_a_broken_analysis_never_loops(self, start, _dispatch, _allowed):
        for _ in range(autopilot.MAX_RESTARTS_PER_HOUR):
            self.run_with(AnalysisRun.Status.FAILED)
        autopilot.advance_onboarding(self.program)
        start.assert_not_called()

    @mock.patch(ALLOWED, return_value="You have used this month's analyses.")
    @mock.patch(DISPATCH)
    @mock.patch(START)
    def test_the_owners_limits_still_apply(self, start, _dispatch, _allowed):
        self.run_with(AnalysisRun.Status.FAILED)
        autopilot.advance_onboarding(self.program)
        start.assert_not_called()
        self.assertIn("used this month", autopilot.onboarding_stage(self.program))

    @mock.patch(DISPATCH)
    def test_a_completed_analysis_still_goes_straight_to_the_report(self, dispatch):
        self.run_with(AnalysisRun.Status.COMPLETE)
        autopilot.advance_onboarding(self.program)
        dispatch.assert_called_once()

    def test_an_active_program_is_never_touched(self):
        GrowthProgram.objects.filter(pk=self.program.pk).update(status=GrowthProgram.Status.ACTIVE)
        self.program.refresh_from_db()
        with mock.patch(START) as start:
            autopilot.advance_onboarding(self.program)
        start.assert_not_called()


class SetupStageTests(GrowthApiTestCase):
    def setUp(self):
        super().setUp()
        self.program = GrowthProgram.objects.create(organization=self.pro_org, created_by_email=PRO_EMAIL)

    def run_with(self, status: str) -> AnalysisRun:
        return AnalysisRun.objects.create(
            organization=self.pro_org, url=self.pro_org.url, email=PRO_EMAIL, status=status
        )

    def test_each_stage_is_described_plainly(self):
        self.run_with(AnalysisRun.Status.SCORING)
        self.assertIn("analysing", autopilot.onboarding_stage(self.program))
        AnalysisRun.objects.all().update(status=AnalysisRun.Status.COMPLETE)
        PositioningReport.objects.create(program=self.program)
        self.assertIn("positioning report", autopilot.onboarding_stage(self.program))
        GrowthProgram.objects.filter(pk=self.program.pk).update(status=GrowthProgram.Status.ACTIVE)
        self.program.refresh_from_db()
        self.assertIn("plan is active", autopilot.onboarding_stage(self.program))

    def test_the_agent_prompt_states_the_approval_model_and_the_real_stage(self):
        self.run_with(AnalysisRun.Status.SCORING)
        prompt = agent._system(self.program)
        self.assertNotIn("fully autonomous", prompt)
        self.assertIn("approval", prompt.lower())
        self.assertIn("analysing", prompt)
        self.assertIn("billing", prompt)  # told never to invent billing-cycle timelines
