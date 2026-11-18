"""
Database triggers on the append-only records (migration 0022), on real Postgres.

Raw SQL can't change or remove an action version, decision, execution event,
ledger row or audit row; deleting a brand erases that brand's rows only, inside
its own transaction. Skipped on SQLite (no triggers there).

Run: TEST_DATABASE_URL=... python manage.py test apps.growth_agent.tests.test_append_only_pg \
        --settings=config.settings.test_postgres
"""

from datetime import timedelta
from unittest import mock, skipUnless

from django.db import DatabaseError, connection, transaction
from django.db.models.signals import pre_delete
from django.test import TransactionTestCase
from django.utils import timezone

from apps.organizations.models import Organization

from ..models import (
    ActionExecutionEvent,
    ActionVersion,
    AgentActionLog,
    ApprovalDecision,
    DispatchAttemptLedger,
    GrowthAction,
    GrowthCycle,
    GrowthProgram,
    GrowthTask,
)
from ..services.control import approvals, dispatch
from ..services.execution import executor, guardrails
from .test_execution_flow import REGISTRY, FakeChannel

PROTECTED = (ActionVersion, ApprovalDecision, ActionExecutionEvent, DispatchAttemptLedger, AgentActionLog)


def table(model) -> str:
    return model._meta.db_table


@skipUnless(connection.vendor == "postgresql", "needs Postgres triggers")
class AppendOnlyTriggerTests(TransactionTestCase):
    def setUp(self):
        self.channel = FakeChannel()
        for patcher in (
            mock.patch.dict(REGISTRY, {"owned_content": self.channel}),
            mock.patch.dict(guardrails.DAILY_CHANNEL_CAPS, {"owned_content": 100}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.org_a = self.brand("a")
        self.org_b = self.brand("b")

    def brand(self, name: str) -> Organization:
        """A brand with one sent action: a version, a decision, a trail, a ledger and audit rows."""
        owner = f"{name}@owner.example"
        org = Organization.objects.create(name=name, url=f"https://{name}.example", owner_email=owner)
        program = GrowthProgram.objects.create(
            organization=org, created_by_email=owner, status=GrowthProgram.Status.ACTIVE
        )
        today = timezone.now().date()
        cycle = GrowthCycle.objects.create(
            program=program, starts_on=today, ends_on=today + timedelta(days=27)
        )
        task = GrowthTask.objects.create(
            cycle=cycle,
            week=1,
            scheduled_for=today,
            channel="owned_content",
            tactic="answer_content",
            title="Page",
            target="x",
        )
        executor.run_task(task.id)
        action = GrowthAction.objects.select_related("current_version").get(task=task)
        version = action.current_version
        approvals.decide(
            program,
            action.public_id,
            approvals.DecisionRequest(
                decision="approved",
                version_number=version.number,
                payload_hash=version.payload_hash,
                actor_email=owner,
            ),
        )
        dispatch.dispatch(action.id)
        return org

    def counts(self, org_id: int) -> dict:
        return {
            ActionVersion: ActionVersion.objects.filter(action__organization_id=org_id).count(),
            ApprovalDecision: ApprovalDecision.objects.filter(organization_id=org_id).count(),
            ActionExecutionEvent: ActionExecutionEvent.objects.filter(organization_id=org_id).count(),
            DispatchAttemptLedger: DispatchAttemptLedger.objects.filter(organization_id=org_id).count(),
            AgentActionLog: AgentActionLog.objects.filter(program__organization_id=org_id).count(),
        }

    def raw(self, sql: str) -> None:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql)

    def test_every_protected_table_has_rows_to_protect(self):
        self.assertTrue(all(self.counts(self.org_a.pk).values()), self.counts(self.org_a.pk))

    def test_raw_updates_and_deletes_are_refused(self):
        for model in PROTECTED:
            name = table(model)
            with self.subTest(model=model.__name__):
                with self.assertRaises(DatabaseError):
                    self.raw(f"UPDATE {name} SET id = id")
                with self.assertRaises(DatabaseError):
                    self.raw(f"DELETE FROM {name}")
        self.assertTrue(all(self.counts(self.org_a.pk).values()))

    def test_an_audit_row_only_loses_its_task_link(self):
        log = AgentActionLog.objects.filter(program__organization=self.org_a).first()
        with self.assertRaises(DatabaseError):
            self.raw(f"UPDATE {table(AgentActionLog)} SET detail = '{{}}'::jsonb WHERE id = {log.id}")
        with self.assertRaises(DatabaseError):
            self.raw(
                f"UPDATE {table(AgentActionLog)} SET outcome = 'failed', task_id = NULL WHERE id = {log.id}"
            )
        self.raw(
            f"UPDATE {table(AgentActionLog)} SET task_id = NULL WHERE id = {log.id} AND task_id IS NOT NULL"
        )

    def test_deleting_a_brand_erases_its_rows_and_only_its_rows(self):
        before_b = self.counts(self.org_b.pk)
        org_a_id = self.org_a.pk
        self.org_a.delete()
        self.assertFalse(any(self.counts(org_a_id).values()))
        self.assertEqual(self.counts(self.org_b.pk), before_b)
        # The permission ended with the deletion's transaction.
        with self.assertRaises(DatabaseError):
            self.raw(f"DELETE FROM {table(ApprovalDecision)} WHERE organization_id = {self.org_b.pk}")

    def test_account_deletion_path_erases_several_brands_at_once(self):
        Organization.objects.filter(pk__in=[self.org_a.pk, self.org_b.pk]).delete()
        self.assertFalse(ApprovalDecision.objects.exists())
        self.assertFalse(AgentActionLog.objects.exists())

    def test_another_brands_rows_stay_protected_during_an_erasure(self):
        other = self.org_b.pk

        def tamper(sender, instance, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {table(ApprovalDecision)} WHERE organization_id = {other}")

        pre_delete.connect(tamper, sender=Organization, dispatch_uid="tamper")
        self.addCleanup(pre_delete.disconnect, sender=Organization, dispatch_uid="tamper")
        with self.assertRaises(DatabaseError):
            self.org_a.delete()
        self.assertTrue(Organization.objects.filter(pk=self.org_a.pk).exists())  # rolled back
        self.assertTrue(ApprovalDecision.objects.filter(organization_id=other).exists())
