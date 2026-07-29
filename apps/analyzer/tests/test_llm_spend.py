"""Tests for per-account LLM spend metering and the budget fuse.

A single analysis costs real money (measured $0.30-$3), so an account can
outspend its own subscription by re-analysing on a loop. These pin the two
behaviours that matter: spend is summed from real recorded charges, and the
fuse fails *open* so a broken meter never blocks a paying customer.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.analyzer.models import AnalysisRun
from apps.analyzer.services import llm_spend

EMAIL = "spender@example.com"


class RecordCostTests(TestCase):
    def test_cost_is_summed_from_recorded_charges(self):
        run = AnalysisRun.objects.create(url="https://a.com", email=EMAIL)
        run.llm_logs = [
            {"purpose": "p", "model": "m", "usage": {"cost": 1.25}},
            {"purpose": "q", "model": "m", "usage": {"cost": 0.75}},
        ]
        total = llm_spend.record_run_cost(run)
        run.refresh_from_db()
        self.assertAlmostEqual(total, 2.0, places=6)
        self.assertAlmostEqual(run.llm_cost_usd, 2.0, places=6)

    def test_a_run_with_no_captured_cost_contributes_zero_not_a_guess(self):
        run = AnalysisRun.objects.create(url="https://a.com", email=EMAIL)
        run.llm_logs = [{"purpose": "p", "model": "m", "usage": {}}]
        self.assertEqual(llm_spend.record_run_cost(run), 0.0)

    def test_recording_never_raises(self):
        """Accounting must not break a completed analysis."""
        run = AnalysisRun.objects.create(url="https://a.com", email=EMAIL)
        with patch.object(llm_spend, "record_run_cost", wraps=llm_spend.record_run_cost):
            with patch("apps.analyzer.pipeline.llm.summarize_llm_logs", side_effect=RuntimeError("boom")):
                self.assertEqual(llm_spend.record_run_cost(run), 0.0)


class SpendWindowTests(TestCase):
    def _run(self, cost, days_ago=0, email=EMAIL):
        run = AnalysisRun.objects.create(url="https://a.com", email=email, llm_cost_usd=cost)
        if days_ago:
            AnalysisRun.objects.filter(pk=run.pk).update(
                created_at=timezone.now() - timedelta(days=days_ago)
            )
        return run

    def test_spend_sums_the_window(self):
        self._run(1.0)
        self._run(2.5)
        self.assertAlmostEqual(llm_spend.spend_since(EMAIL), 3.5, places=6)

    def test_runs_outside_the_window_are_excluded(self):
        self._run(5.0, days_ago=40)
        self._run(1.0)
        self.assertAlmostEqual(llm_spend.spend_since(EMAIL), 1.0, places=6)

    def test_other_accounts_do_not_count(self):
        self._run(9.0, email="someone@else.com")
        self._run(1.0)
        self.assertAlmostEqual(llm_spend.spend_since(EMAIL), 1.0, places=6)

    def test_email_matching_is_case_insensitive(self):
        self._run(2.0, email="Spender@Example.com")
        self.assertAlmostEqual(llm_spend.spend_since(EMAIL), 2.0, places=6)

    def test_anonymous_runs_have_no_account_spend(self):
        self.assertEqual(llm_spend.spend_since(""), 0.0)

    def test_top_spenders_ranks_by_cost(self):
        self._run(1.0, email="small@x.com")
        self._run(9.0, email="big@x.com")
        rows = llm_spend.top_spenders()
        self.assertEqual(rows[0]["email"], "big@x.com")


class BudgetFuseTests(TestCase):
    def _spend(self, cost):
        AnalysisRun.objects.create(url="https://a.com", email=EMAIL, llm_cost_usd=cost)

    def test_under_the_limit_is_allowed(self):
        self._spend(5.0)
        with patch.object(llm_spend, "limit_for", return_value=25.0):
            status = llm_spend.check_budget(EMAIL)
        self.assertTrue(status.allowed)
        self.assertAlmostEqual(status.remaining_usd, 20.0, places=6)

    def test_over_the_limit_is_blocked(self):
        self._spend(30.0)
        with patch.object(llm_spend, "limit_for", return_value=25.0):
            status = llm_spend.check_budget(EMAIL)
        self.assertFalse(status.allowed)
        self.assertEqual(status.remaining_usd, 0.0)

    def test_zero_limit_means_uncapped(self):
        """Internal and grandfathered accounts must never be fused off."""
        self._spend(500.0)
        with patch.object(llm_spend, "limit_for", return_value=0.0):
            status = llm_spend.check_budget(EMAIL)
        self.assertTrue(status.allowed)
        self.assertTrue(status.uncapped)

    def test_a_broken_meter_fails_open(self):
        """A crash in accounting must not stop a paying customer working."""
        with patch.object(llm_spend, "spend_since", side_effect=RuntimeError("db down")):
            status = llm_spend.check_budget(EMAIL)
        self.assertTrue(status.allowed)

    def test_exactly_at_the_limit_is_blocked(self):
        self._spend(25.0)
        with patch.object(llm_spend, "limit_for", return_value=25.0):
            self.assertFalse(llm_spend.check_budget(EMAIL).allowed)


class PlanBudgetTests(TestCase):
    def test_each_plan_declares_a_budget(self):
        from apps.accounts.models import PLAN_LIMITS

        for plan, limits in PLAN_LIMITS.items():
            with self.subTest(plan=plan):
                self.assertIn("max_llm_spend_usd", limits)

    def test_business_plan_is_uncapped(self):
        from apps.accounts.models import PLAN_LIMITS

        self.assertEqual(PLAN_LIMITS["business"]["max_llm_spend_usd"], 0.0)

    def test_budget_is_env_overridable(self):
        from apps.accounts.models import _plan_budget

        with patch.dict("os.environ", {"LLM_BUDGET_USD_STARTER": "12.5"}):
            self.assertEqual(_plan_budget("STARTER", 25.0), 12.5)

    def test_malformed_env_falls_back_to_the_default(self):
        from apps.accounts.models import _plan_budget

        with patch.dict("os.environ", {"LLM_BUDGET_USD_STARTER": "not-a-number"}):
            self.assertEqual(_plan_budget("STARTER", 25.0), 25.0)
