"""
Approvals that run out, and approvals the owner withdraws.

An approval counts for ``APPROVAL_TTL`` (14 days). After that the action is
expired and never sent. The sender checks this itself, so an expired approval
can't go out even if the expiry sweep never ran. The owner can also withdraw
(revoke) an approval before sending starts.

Run: python manage.py test apps.growth_agent.tests.test_expiry_revoke --settings=config.settings.test
"""

from datetime import timedelta
from unittest import mock

from django.utils import timezone

from ..models import AgentActionLog, ApprovalDecision, GrowthAction, GrowthTask
from ..services.control import approvals, expiry
from ..services.execution import executor
from .test_api import PRO_EMAIL
from .test_approval_flow import ApprovalFlowTestCase

JUST_BEFORE = approvals.APPROVAL_TTL - timedelta(minutes=1)
JUST_AFTER = approvals.APPROVAL_TTL + timedelta(minutes=1)


def later(delta: timedelta):
    """Pretend the clock moved forward by ``delta`` for the approval checks."""
    return mock.patch.object(approvals, "_now", return_value=timezone.now() + delta)


class ExpiryTests(ApprovalFlowTestCase):
    def setUp(self):
        super().setUp()
        self.approve()
        self.reload()

    def test_an_approval_is_valid_until_it_expires(self):
        with later(JUST_BEFORE):
            self.assertTrue(approvals.has_valid_approval(self.action))
        with later(JUST_AFTER):
            self.assertFalse(approvals.has_valid_approval(self.action))

    def test_the_review_shows_when_the_approval_expires(self):
        response = self.signed("GET", f"{self.base()}/actions/{self.action.public_id}/", PRO_EMAIL)
        expires = response.json()["action"]["approval_expires_at"]
        decided = ApprovalDecision.objects.get(action=self.action).created_at
        self.assertEqual(expires, (decided + approvals.APPROVAL_TTL).isoformat().replace("+00:00", "Z"))

    def test_the_sender_refuses_an_expired_approval_even_without_the_sweep(self):
        with later(JUST_AFTER):
            self.send()
        self.assertEqual(self.channel.executed, [])
        self.assertEqual(self.action.state, GrowthAction.State.EXPIRED)
        self.assertEqual(self.task.status, GrowthTask.Status.SKIPPED)

    def test_a_valid_approval_is_still_sent(self):
        with later(JUST_BEFORE):
            self.send()
        self.assertEqual(len(self.channel.executed), 1)

    def test_the_sweep_expires_old_approvals_only(self):
        fresh_task = self.make_task(title="Fresh")
        from_executor = self.prepare_and_approve(fresh_task)
        with later(JUST_AFTER):
            self.assertEqual(expiry.expire_due(), 2)
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.EXPIRED)
        from_executor.refresh_from_db()
        self.assertEqual(from_executor.state, GrowthAction.State.EXPIRED)
        with later(JUST_BEFORE):
            self.assertEqual(expiry.expire_due(), 0)

    def test_the_sweep_leaves_a_fresh_approval_alone(self):
        with later(JUST_BEFORE):
            self.assertEqual(expiry.expire_due(), 0)
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.APPROVED)

    def test_expiry_is_recorded_as_a_system_decision_and_audited(self):
        with later(JUST_AFTER):
            expiry.expire_due()
        record = ApprovalDecision.objects.filter(action=self.action).last()
        self.assertEqual(record.decision, ApprovalDecision.Decision.EXPIRED)
        self.assertEqual(record.actor_role, "system")
        self.assertEqual(record.actor_email, "")
        self.assertTrue(AgentActionLog.objects.filter(action="approval_expired").exists())

    def test_an_expired_action_can_no_longer_be_approved_or_rejected(self):
        with later(JUST_AFTER):
            expiry.expire_due()
        self.assertEqual(self.approve().status_code, 409)
        self.assertEqual(self.reject().status_code, 409)

    def prepare_and_approve(self, task) -> GrowthAction:
        executor.run_task(task.id)
        action = GrowthAction.objects.select_related("current_version").get(task=task)
        version = action.current_version
        approvals.decide(
            self.program,
            action.public_id,
            approvals.DecisionRequest("approved", version.number, version.payload_hash, PRO_EMAIL),
        )
        return action


class RevokeTests(ApprovalFlowTestCase):
    def revoke(self, **kwargs):
        return self.decide("revoke", **kwargs)

    def test_owner_withdraws_an_approval_before_it_is_sent(self):
        self.approve()
        response = self.revoke(reason="Not this week.")
        self.assertEqual(response.status_code, 200)
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.REVOKED)
        self.assertEqual(self.task.status, GrowthTask.Status.SKIPPED)
        self.assertIn("withdrawn", self.task.result_payload["skipped"])

    def test_a_revoked_approval_is_never_sent(self):
        self.approve()
        self.revoke()
        self.send()
        self.assertEqual(self.channel.executed, [])

    def test_only_an_approved_action_can_be_revoked(self):
        response = self.revoke()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "illegal_state")

    def test_a_sent_action_can_no_longer_be_revoked(self):
        self.approve()
        self.send()
        self.assertEqual(self.revoke().status_code, 409)

    def test_revoking_twice_records_it_once(self):
        self.approve()
        self.revoke()
        self.assertEqual(self.revoke().status_code, 200)
        revoked = ApprovalDecision.objects.filter(action=self.action, decision="revoked")
        self.assertEqual(revoked.count(), 1)

    def test_the_revocation_is_recorded_with_who_did_it(self):
        self.approve()
        self.revoke()
        record = ApprovalDecision.objects.filter(action=self.action).last()
        self.assertEqual(record.decision, ApprovalDecision.Decision.REVOKED)
        self.assertEqual(record.actor_email, PRO_EMAIL)
        entry = AgentActionLog.objects.filter(action="revoked").get()
        self.assertEqual(entry.actor_email, PRO_EMAIL)
