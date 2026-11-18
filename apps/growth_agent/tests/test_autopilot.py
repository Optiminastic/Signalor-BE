"""
Onboarding autopilot: a new program moves to an active plan with no human step.

Run: python manage.py test apps.growth_agent.tests.test_autopilot --settings=config.settings.test
"""

from datetime import timedelta
from unittest import mock

from django.utils import timezone

from apps.analyzer.models import AnalysisRun

from ..models import GrowthCycle, GrowthProgram, PositioningReport
from ..services import autopilot
from .test_api import PRO_EMAIL, GrowthApiTestCase
from .test_planning import _report

DISPATCH = "apps.growth_agent.services.autopilot.dispatch_positioning_report"


class AutopilotTests(GrowthApiTestCase):
    def setUp(self):
        super().setUp()
        self.program = GrowthProgram.objects.create(organization=self.pro_org, created_by_email=PRO_EMAIL)

    def _complete_run(self) -> AnalysisRun:
        return AnalysisRun.objects.create(
            organization=self.pro_org,
            url=self.pro_org.url,
            email=PRO_EMAIL,
            status=AnalysisRun.Status.COMPLETE,
        )

    @mock.patch(DISPATCH)
    def test_without_a_completed_analysis_nothing_happens(self, dispatch):
        autopilot.advance_onboarding(self.program)
        dispatch.assert_not_called()
        self.assertFalse(PositioningReport.objects.exists())

    @mock.patch(DISPATCH)
    def test_completed_analysis_starts_the_report_once(self, dispatch):
        self._complete_run()
        autopilot.advance_onboarding(self.program)
        autopilot.advance_onboarding(self.program)  # pending report is reused, not rebuilt
        dispatch.assert_called_once()
        self.assertEqual(PositioningReport.objects.count(), 1)

    @mock.patch(DISPATCH)
    def test_complete_report_creates_the_plan_and_activates(self, dispatch):
        PositioningReport.objects.create(
            program=self.program, status=PositioningReport.Status.COMPLETE, **_report()
        )
        autopilot.advance_onboarding(self.program)
        self.program.refresh_from_db()
        self.assertEqual(self.program.status, GrowthProgram.Status.ACTIVE)
        self.assertEqual(GrowthCycle.objects.count(), 1)
        dispatch.assert_not_called()

    @mock.patch(DISPATCH)
    def test_recent_failure_is_not_retried_on_every_visit(self, dispatch):
        run = self._complete_run()
        PositioningReport.objects.create(
            program=self.program, analysis_run=run, status=PositioningReport.Status.FAILED
        )
        autopilot.advance_onboarding(self.program)
        dispatch.assert_not_called()
        old = timezone.now() - autopilot.FAILED_REPORT_RETRY_AFTER - timedelta(minutes=1)
        PositioningReport.objects.update(created_at=old, updated_at=old)
        autopilot.advance_onboarding(self.program)
        dispatch.assert_called_once()

    @mock.patch(DISPATCH)
    def test_active_program_is_left_alone(self, dispatch):
        self._complete_run()
        GrowthProgram.objects.filter(pk=self.program.pk).update(status=GrowthProgram.Status.ACTIVE)
        self.program.refresh_from_db()
        autopilot.advance_onboarding(self.program)
        dispatch.assert_not_called()

    @mock.patch(DISPATCH)
    def test_opening_agent_mode_advances_onboarding(self, dispatch):
        self._complete_run()
        self._body = {"org_slug": self.pro_org.slug}
        response = self.signed("POST", "/api/growth/programs/", PRO_EMAIL)
        self.assertEqual(response.status_code, 200)
        dispatch.assert_called_once()
