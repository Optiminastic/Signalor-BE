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


class CostScopeTests(TestCase):
    """Work that runs outside a run's log-collection window must still be metered.

    The competitive-prompt fire is dispatched to a daemon thread after the run is
    finalized and its logs drained, so its calls used to land on a None log and
    never reach llm_cost_usd or the 30-day budget window.
    """

    def test_scope_accumulates_call_cost(self):
        from apps.analyzer.pipeline import llm

        with llm.cost_scope() as spend:
            llm._record_scope_cost({"cost": 0.01})
            llm._record_scope_cost({"cost": 0.02})
        self.assertAlmostEqual(spend["cost"], 0.03, places=6)
        self.assertEqual(spend["calls"], 2)

    def test_costs_outside_the_scope_are_not_counted(self):
        from apps.analyzer.pipeline import llm

        with llm.cost_scope() as spend:
            pass
        llm._record_scope_cost({"cost": 5.0})
        self.assertEqual(spend["cost"], 0.0)

    def test_nested_scopes_both_see_inner_calls(self):
        from apps.analyzer.pipeline import llm

        with llm.cost_scope() as outer:
            llm._record_scope_cost({"cost": 1.0})
            with llm.cost_scope() as inner:
                llm._record_scope_cost({"cost": 2.0})
        self.assertAlmostEqual(inner["cost"], 2.0, places=6)
        self.assertAlmostEqual(outer["cost"], 3.0, places=6)

    def test_missing_cost_is_treated_as_zero(self):
        from apps.analyzer.pipeline import llm

        with llm.cost_scope() as spend:
            llm._record_scope_cost({})
            llm._record_scope_cost(None)
        self.assertEqual(spend["cost"], 0.0)

    def test_propagate_carries_the_scope_into_a_pool_worker(self):
        """ThreadPoolExecutor does not inherit contextvars on its own."""
        from concurrent.futures import ThreadPoolExecutor

        from apps.analyzer.pipeline import llm

        def _work(_):
            llm._record_scope_cost({"cost": 0.25})

        with llm.cost_scope() as spend:
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(llm.propagate(_work), range(2)))
        self.assertAlmostEqual(spend["cost"], 0.5, places=6)

    def test_without_propagate_a_pool_worker_is_not_counted(self):
        """Pins the reason propagate() has to exist."""
        from concurrent.futures import ThreadPoolExecutor

        from apps.analyzer.pipeline import llm

        def _work(_):
            llm._record_scope_cost({"cost": 0.25})

        with llm.cost_scope() as spend:
            with ThreadPoolExecutor(max_workers=1) as pool:
                list(pool.map(_work, range(2)))
        self.assertEqual(spend["cost"], 0.0)


class FinalAccountingTests(TestCase):
    """The run-level ``finally`` must not undo accounting a sub-path already did.

    ``get_collected_logs()`` clears the collector as it reads. The partial
    analysis path drains and records its own spend before returning, so a
    second unconditional drain in ``finally`` would overwrite ``llm_logs`` with
    an empty list and reset ``llm_cost_usd`` to zero - silently dropping that
    run out of the 30-day budget window.
    """

    def _run(self):
        return AnalysisRun.objects.create(url="https://a.com", email=EMAIL)

    def test_an_empty_drain_does_not_erase_already_recorded_spend(self):
        from apps.analyzer import tasks

        run = self._run()
        # Mirror _run_partial_analysis: it saves the drained logs, then meters them.
        run.llm_logs = [{"purpose": "p", "model": "m", "usage": {"cost": 1.5}}]
        run.save(update_fields=["llm_logs"])
        llm_spend.record_run_cost(run)

        # Simulate the partial path: logs already drained, so the collector is empty.
        with patch.object(tasks, "get_collected_logs", return_value=[]):
            with patch.object(tasks, "_end_trace"):
                tasks._finalize_accounting(run, run.id)

        run.refresh_from_db()
        self.assertEqual(len(run.llm_logs), 1)
        self.assertAlmostEqual(run.llm_cost_usd, 1.5, places=6)
        self.assertAlmostEqual(llm_spend.spend_since(EMAIL), 1.5, places=6)

    def test_a_non_empty_drain_is_recorded(self):
        from apps.analyzer import tasks

        run = self._run()
        logs = [{"purpose": "p", "model": "m", "usage": {"cost": 0.75}}]
        with patch.object(tasks, "get_collected_logs", return_value=logs):
            with patch.object(tasks, "_end_trace"):
                tasks._finalize_accounting(run, run.id)

        run.refresh_from_db()
        self.assertAlmostEqual(run.llm_cost_usd, 0.75, places=6)

    def test_accounting_failure_does_not_stop_the_trace_flush(self):
        """A broken meter must not leave the Langfuse buffer unflushed."""
        from apps.analyzer import tasks

        run = self._run()
        with patch.object(tasks, "get_collected_logs", side_effect=RuntimeError("boom")):
            with patch.object(tasks, "_end_trace") as end_trace:
                tasks._finalize_accounting(run, run.id)
        end_trace.assert_called_once_with(run.id)

    def test_a_second_call_does_not_write_over_the_first(self):
        """The success path records early, so the finally call must be a no-op."""
        from apps.analyzer import tasks

        run = self._run()
        logs = [{"purpose": "p", "model": "m", "usage": {"cost": 0.9}}]
        with patch.object(tasks, "get_collected_logs", return_value=logs):
            tasks._record_run_spend(run, run.id)
        # Second drain is empty, exactly as the real collector behaves.
        with patch.object(tasks, "get_collected_logs", return_value=[]):
            with patch.object(tasks, "_end_trace"):
                tasks._finalize_accounting(run, run.id)

        run.refresh_from_db()
        self.assertAlmostEqual(run.llm_cost_usd, 0.9, places=6)


class CompetitiveDispatchBudgetTests(TestCase):
    """The competitive-prompt fuse must see the run that just finished.

    ~40 billable calls are dispatched once an analysis completes. The gate reads
    llm_cost_usd back from the database, so recording this run's cost after the
    dispatch let an account sitting just under its cap spend straight through it.
    """

    def _run(self, cost_recorded=0.0):
        return AnalysisRun.objects.create(
            url="https://a.com", email=EMAIL, llm_cost_usd=cost_recorded
        )

    def test_this_runs_cost_counts_against_the_dispatch_budget(self):
        from apps.analyzer import tasks

        AnalysisRun.objects.create(url="https://old.com", email=EMAIL, llm_cost_usd=19.5)
        run = self._run()
        logs = [{"purpose": "p", "model": "m", "usage": {"cost": 1.5}}]

        with patch.object(tasks, "get_collected_logs", return_value=logs):
            tasks._record_run_spend(run, run.id)

        with patch.object(llm_spend, "limit_for", return_value=20.0):
            self.assertFalse(tasks._budget_status(EMAIL).allowed)

    def test_an_account_still_under_its_cap_is_allowed(self):
        from apps.analyzer import tasks

        AnalysisRun.objects.create(url="https://old.com", email=EMAIL, llm_cost_usd=5.0)
        run = self._run()
        logs = [{"purpose": "p", "model": "m", "usage": {"cost": 1.5}}]

        with patch.object(tasks, "get_collected_logs", return_value=logs):
            tasks._record_run_spend(run, run.id)

        with patch.object(llm_spend, "limit_for", return_value=20.0):
            self.assertTrue(tasks._budget_status(EMAIL).allowed)

    def test_the_run_records_its_spend_before_finishing(self):
        """The ordering guard that mattered when the run dispatched more billable work.

        Competitive prompts no longer fire from the run at all (see
        test_competitive_prompts), so there is nothing left to race. What still
        matters is that the run's own cost reaches llm_cost_usd on the success
        path, not only in the finally block — the on-demand generate endpoint
        reads that value back when it checks the budget.
        """
        import inspect

        from apps.analyzer import tasks

        source = inspect.getsource(tasks.run_single_page_analysis)
        self.assertIn("_record_run_spend(run, run_id)", source)
        self.assertNotIn("_generate_and_fire_competitive_prompts(run)", source)

    def test_background_spend_is_not_clobbered_by_the_run_total(self):
        """record_run_cost writes absolutely; _add_background_spend increments."""
        from apps.analyzer import tasks

        run = self._run()
        logs = [{"purpose": "p", "model": "m", "usage": {"cost": 1.0}}]
        with patch.object(tasks, "get_collected_logs", return_value=logs):
            tasks._record_run_spend(run, run.id)
        tasks._add_background_spend(run.id, {"cost": 0.4, "calls": 40})
        # The finally call lands last and must not undo the increment.
        with patch.object(tasks, "get_collected_logs", return_value=[]):
            with patch.object(tasks, "_end_trace"):
                tasks._finalize_accounting(run, run.id)

        run.refresh_from_db()
        self.assertAlmostEqual(run.llm_cost_usd, 1.4, places=6)


class BackgroundSpendTests(TestCase):
    def test_background_cost_is_added_to_the_run(self):
        from apps.analyzer.tasks import _add_background_spend

        run = AnalysisRun.objects.create(url="https://a.com", email=EMAIL, llm_cost_usd=1.0)
        _add_background_spend(run.id, {"cost": 0.5, "calls": 4})
        run.refresh_from_db()
        self.assertAlmostEqual(run.llm_cost_usd, 1.5, places=6)

    def test_background_spend_reaches_the_budget_window(self):
        from apps.analyzer.tasks import _add_background_spend

        run = AnalysisRun.objects.create(url="https://a.com", email=EMAIL, llm_cost_usd=0.0)
        _add_background_spend(run.id, {"cost": 2.0, "calls": 1})
        self.assertAlmostEqual(llm_spend.spend_since(EMAIL), 2.0, places=6)

    def test_zero_cost_is_a_no_op(self):
        from apps.analyzer.tasks import _add_background_spend

        run = AnalysisRun.objects.create(url="https://a.com", email=EMAIL, llm_cost_usd=1.0)
        _add_background_spend(run.id, {"cost": 0.0})
        run.refresh_from_db()
        self.assertAlmostEqual(run.llm_cost_usd, 1.0, places=6)
