"""Tests for per-brand auto-fix / analysis count quotas and auto-fix spend metering.

Auto-fix generation spends real LLM money but previously had no count limit and
was invisible to the USD budget fuse. These pin the three new behaviours:

* count quotas (30-day / daily / per-recommendation regen) block past the cap
  and are scoped per brand,
* rows that never call the LLM (manual, verification, approve) do not consume
  the generation quota,
* generation cost is folded into ``AnalysisRun.llm_cost_usd`` so the existing
  budget fuse can see it.
"""

import os
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.accounts.subscription_utils import (
    analysis_count_limit_reached,
    autofix_limit_reached,
    autofix_regen_limit_reached,
)
from apps.analyzer.models import AnalysisRun, AutoFixJob, Recommendation
from apps.integrations.models import Integration
from apps.organizations.models import Organization

OWNER = "owner@brand-example.com"

# Plan-limit enforcement is env-driven; force it on (and payment on) for quota
# tests regardless of what the local .env carries.
ENFORCED_ENV = {"ENFORCE_PLAN_LIMITS": "true", "DISABLE_PAYMENT": ""}


def _make_fixtures():
    org = Organization.objects.create(name="Acme", owner_email=OWNER)
    run = AnalysisRun.objects.create(organization=org, url="https://acme.com", email=OWNER)
    rec = Recommendation.objects.create(
        analysis_run=run,
        pillar="content",
        priority="high",
        title="Improve meta description",
        description="d",
        action="a",
        category="content",
    )
    return org, run, rec


def _generation_job(run, rec, days_ago=0, **overrides):
    """One persisted auto-fix generation (the unit the quotas count)."""
    fields = {"fix_type": "meta", "status": "success"}
    fields.update(overrides)
    job = AutoFixJob.objects.create(analysis_run=run, recommendation=rec, **fields)
    if days_ago:
        AutoFixJob.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    return job


@patch.dict(os.environ, ENFORCED_ENV)
class AutofixCountQuotaTests(TestCase):
    def setUp(self):
        self.org, self.run, self.rec = _make_fixtures()

    def test_under_the_cap_is_allowed(self):
        _generation_job(self.run, self.rec)
        reached, msg = autofix_limit_reached(OWNER, self.org)
        self.assertFalse(reached)
        self.assertEqual(msg, "")

    def test_monthly_cap_blocks_past_30_generations(self):
        # Older than a day so the daily cap stays out of the way; still inside
        # the 30-day window.
        for _ in range(30):
            _generation_job(self.run, self.rec, days_ago=2)
        reached, msg = autofix_limit_reached(OWNER, self.org)
        self.assertTrue(reached)
        self.assertIn("30 auto-fixes", msg)

    def test_generations_older_than_the_window_do_not_count(self):
        for _ in range(30):
            _generation_job(self.run, self.rec, days_ago=31)
        reached, _ = autofix_limit_reached(OWNER, self.org)
        self.assertFalse(reached)

    def test_daily_cap_blocks_a_single_day_burst(self):
        for _ in range(10):
            _generation_job(self.run, self.rec)
        reached, msg = autofix_limit_reached(OWNER, self.org)
        self.assertTrue(reached)
        self.assertIn("per day", msg)

    def test_batch_that_would_cross_the_cap_is_blocked_up_front(self):
        for _ in range(28):
            _generation_job(self.run, self.rec, days_ago=2)
        reached, _ = autofix_limit_reached(OWNER, self.org, additional=3)
        self.assertTrue(reached)
        reached, _ = autofix_limit_reached(OWNER, self.org, additional=2)
        self.assertFalse(reached)

    def test_non_llm_rows_do_not_consume_the_quota(self):
        for _ in range(40):
            _generation_job(self.run, self.rec, fix_type="verification")
            _generation_job(self.run, self.rec, fix_type="manual")
            _generation_job(self.run, self.rec, payload_sent={"source": "approve"})
        reached, _ = autofix_limit_reached(OWNER, self.org)
        self.assertFalse(reached)

    def test_quota_is_scoped_per_brand(self):
        other_org = Organization.objects.create(name="Other", owner_email=OWNER)
        other_run = AnalysisRun.objects.create(organization=other_org, url="https://other.com", email=OWNER)
        other_rec = Recommendation.objects.create(
            analysis_run=other_run,
            pillar="content",
            priority="high",
            title="t",
            description="d",
            action="a",
            category="content",
        )
        for _ in range(30):
            _generation_job(other_run, other_rec, days_ago=2)
        # The sibling brand burned its quota; this brand is untouched.
        reached, _ = autofix_limit_reached(OWNER, self.org)
        self.assertFalse(reached)

    def test_internal_emails_are_never_capped(self):
        for _ in range(50):
            _generation_job(self.run, self.rec)
        reached, _ = autofix_limit_reached("dev@optiminastic.com", self.org)
        self.assertFalse(reached)

    def test_missing_org_fails_closed(self):
        reached, msg = autofix_limit_reached(OWNER, None)
        self.assertTrue(reached)
        self.assertIn("brand", msg.lower())

    def test_toggle_off_disables_the_quota(self):
        for _ in range(50):
            _generation_job(self.run, self.rec)
        with patch.dict(os.environ, {"ENFORCE_PLAN_LIMITS": "false"}):
            reached, _ = autofix_limit_reached(OWNER, self.org)
        self.assertFalse(reached)


@patch.dict(os.environ, ENFORCED_ENV)
class RegenQuotaTests(TestCase):
    def setUp(self):
        self.org, self.run, self.rec = _make_fixtures()

    def _preview(self, fix_type="meta"):
        return _generation_job(self.run, self.rec, status=AutoFixJob.Status.PREVIEW, fix_type=fix_type)

    def test_initial_generation_plus_three_regens_then_blocked(self):
        for n in range(4):  # 1 initial + 3 regens
            reached, _ = autofix_regen_limit_reached(OWNER, self.rec)
            self.assertFalse(reached, f"generation {n + 1} should be allowed")
            self._preview()
        reached, msg = autofix_regen_limit_reached(OWNER, self.rec)
        self.assertTrue(reached)
        self.assertIn("3 regenerations", msg)

    def test_manual_previews_never_consume_regens(self):
        for _ in range(10):
            self._preview(fix_type="manual")
        reached, _ = autofix_regen_limit_reached(OWNER, self.rec)
        self.assertFalse(reached)


@patch.dict(os.environ, ENFORCED_ENV)
class AnalysisCountQuotaTests(TestCase):
    def setUp(self):
        self.org, self.run, self.rec = _make_fixtures()

    def _analysis(self, days_ago=0, org=None):
        run = AnalysisRun.objects.create(organization=org or self.org, url="https://acme.com", email=OWNER)
        if days_ago:
            AnalysisRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        return run

    def test_starter_is_blocked_at_eight_analyses(self):
        for _ in range(7):  # setUp already created one
            self._analysis()
        reached, msg = analysis_count_limit_reached(OWNER, self.org)
        self.assertTrue(reached)
        self.assertIn("8 analyses", msg)

    def test_runs_outside_the_window_do_not_count(self):
        for _ in range(10):
            self._analysis(days_ago=31)
        reached, _ = analysis_count_limit_reached(OWNER, self.org)
        self.assertFalse(reached)

    def test_scoped_per_brand(self):
        other_org = Organization.objects.create(name="Other", owner_email=OWNER)
        for _ in range(10):
            self._analysis(org=other_org)
        reached, _ = analysis_count_limit_reached(OWNER, self.org)
        self.assertFalse(reached)


class SpendMeteringTests(TestCase):
    """Auto-fix generation cost must land on the run's ledger, where the
    30-day budget fuse (services.llm_spend) sums it."""

    def setUp(self):
        self.org, self.run, self.rec = _make_fixtures()

    def test_meter_folds_cost_into_the_run(self):
        from apps.analyzer.auto_fix import _meter_autofix_spend

        _meter_autofix_spend(self.run, {"cost": 0.07, "calls": 2}, "generate:meta")
        self.run.refresh_from_db()
        self.assertAlmostEqual(self.run.llm_cost_usd, 0.07, places=6)

    def test_zero_cost_is_a_noop(self):
        from apps.analyzer.auto_fix import _meter_autofix_spend

        _meter_autofix_spend(self.run, {"cost": 0.0, "calls": 0}, "generate:meta")
        self.run.refresh_from_db()
        self.assertEqual(self.run.llm_cost_usd, 0.0)

    def test_metered_generation_records_what_the_scope_saw(self):
        from apps.analyzer import auto_fix
        from core.llm import client as llm

        def _fake_generate(fix_type, run, recommendation):
            llm._record_scope_cost({"cost": 0.05})
            return "generated", None

        with patch.object(auto_fix, "_generate_fix_content", side_effect=_fake_generate):
            content, err = auto_fix._generate_fix_content_metered("meta", self.run, self.rec)

        self.assertEqual((content, err), ("generated", None))
        self.run.refresh_from_db()
        self.assertAlmostEqual(self.run.llm_cost_usd, 0.05, places=6)


@patch.dict(os.environ, ENFORCED_ENV)
class PreviewEndpointQuotaTests(TestCase):
    """The preview endpoint is the regen abuse surface — gate it end to end."""

    def setUp(self):
        self.org, self.run, self.rec = _make_fixtures()
        self.integration = Integration.objects.create(
            organization=self.org, provider=Integration.Provider.WORDPRESS
        )

    def _post_preview(self, force=True):
        with patch(
            "apps.analyzer.integration_resolve.resolve_store_integration_for_run",
            return_value=self.integration,
        ):
            return self.client.post(
                f"/api/analyzer/runs/s/{self.run.slug}/auto-fix/preview/",
                {"recommendation_id": self.rec.id, "email": OWNER, "force": force},
                content_type="application/json",
            )

    def test_regen_cap_returns_403_plan_limit(self):
        for _ in range(4):
            _generation_job(self.run, self.rec, status=AutoFixJob.Status.PREVIEW)
        resp = self._post_preview(force=True)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("code"), "plan_limit_exceeded")

    def test_each_generation_persists_its_own_audit_row(self):
        preview = {"status": "preview", "fix_type": "meta", "preview": "x", "full_content": "x"}
        with patch("apps.analyzer.auto_fix.generate_fix_preview", return_value=preview):
            self.assertEqual(self._post_preview(force=True).status_code, 200)
            self.assertEqual(self._post_preview(force=True).status_code, 200)
        rows = AutoFixJob.objects.filter(recommendation=self.rec, status=AutoFixJob.Status.PREVIEW)
        self.assertEqual(rows.count(), 2)

    def test_cached_preview_is_served_without_a_new_row_or_quota_hit(self):
        _generation_job(
            self.run,
            self.rec,
            status=AutoFixJob.Status.PREVIEW,
            response_data={"status": "preview", "fix_type": "meta", "preview": "x"},
        )
        resp = self._post_preview(force=False)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("cached"))
        self.assertEqual(
            AutoFixJob.objects.filter(recommendation=self.rec, status=AutoFixJob.Status.PREVIEW).count(),
            1,
        )

    def test_over_budget_account_gets_402(self):
        # Starter USD fuse is $25/30d; a prior expensive run trips it.
        AnalysisRun.objects.create(organization=self.org, url="https://acme.com", email=OWNER, llm_cost_usd=30.0)
        resp = self._post_preview(force=True)
        self.assertEqual(resp.status_code, 402)


@patch.dict(os.environ, ENFORCED_ENV)
class ApplyEndpointQuotaTests(TestCase):
    def setUp(self):
        self.org, self.run, self.rec = _make_fixtures()
        self.integration = Integration.objects.create(
            organization=self.org, provider=Integration.Provider.WORDPRESS
        )

    def test_batch_apply_past_the_cap_returns_403(self):
        for _ in range(30):
            _generation_job(self.run, self.rec, days_ago=2)
        with patch(
            "apps.analyzer.integration_resolve.resolve_store_integration_for_run",
            return_value=self.integration,
        ):
            resp = self.client.post(
                f"/api/analyzer/runs/s/{self.run.slug}/auto-fix/",
                {"recommendation_ids": [self.rec.id], "email": OWNER},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("code"), "plan_limit_exceeded")

    def test_approve_audit_row_is_marked_as_apply_not_generation(self):
        result = {"status": "success", "message": "ok"}
        with (
            patch(
                "apps.analyzer.integration_resolve.resolve_store_integration_for_run",
                return_value=self.integration,
            ),
            patch("apps.analyzer.auto_fix.apply_approved_fix", return_value=result),
        ):
            resp = self.client.post(
                f"/api/analyzer/runs/s/{self.run.slug}/auto-fix/approve/",
                {"recommendation_id": self.rec.id, "content": "c", "fix_type": "meta"},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        job = AutoFixJob.objects.filter(recommendation=self.rec).latest("created_at")
        self.assertEqual(job.payload_sent.get("source"), "approve")
        # And therefore it does not consume the generation quota.
        reached, _ = autofix_limit_reached(OWNER, self.org)
        self.assertFalse(reached)
