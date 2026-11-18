"""
A background check that dies without finishing (process restarted) must not
leave the panel "checking" forever or hold the once-a-day limit.

Run: python manage.py test apps.growth_agent.tests.test_run_guard --settings=config.settings.test
"""

from datetime import timedelta
from unittest import mock

from django.utils import timezone

from ..models import AgentActionLog
from ..services import run_guard
from ..services.rank_tracking import check
from ..services.rank_tracking.report import report
from .db_helpers import append_only_bypass
from .test_rank_tracking import RankBase

DAY = timedelta(hours=24)


class RunGuardTests(RankBase):
    def log(self, action, minutes_ago):
        row = AgentActionLog.objects.create(program=self.program, action=action, outcome="succeeded")
        with append_only_bypass():
            AgentActionLog.objects.filter(pk=row.pk).update(
                created_at=timezone.now() - timedelta(minutes=minutes_ago)
            )

    def test_a_fresh_unfinished_run_is_running_and_blocks_a_second_one(self):
        self.log(check.STARTED, 5)
        self.assertTrue(run_guard.is_running(self.program, check.STARTED, check.DONE))
        self.assertFalse(run_guard.may_start(self.program, check.STARTED, check.DONE, DAY))

    def test_a_lost_run_stops_showing_and_frees_check_now(self):
        self.log(check.STARTED, 45)
        self.assertFalse(report(self.program)["checking"])
        with mock.patch("apps.growth_agent.analysis_tasks.dispatch_rank_check"):
            self.assertTrue(check.start(self.program, guard=check.START_GUARD))

    def test_a_finished_run_still_holds_the_daily_limit(self):
        self.log(check.STARTED, 60)
        self.log(check.DONE, 55)
        self.assertFalse(run_guard.is_running(self.program, check.STARTED, check.DONE))
        self.assertFalse(run_guard.may_start(self.program, check.STARTED, check.DONE, DAY))

    def test_after_the_guard_a_new_run_may_start(self):
        self.log(check.STARTED, 60 * 25)
        self.log(check.DONE, 60 * 25 - 2)
        self.assertTrue(run_guard.may_start(self.program, check.STARTED, check.DONE, DAY))
