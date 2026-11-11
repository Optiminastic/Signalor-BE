"""
Closing an action whose send outcome is unknown.

A send that was attempted but never confirmed parks the action in
``needs_reconciliation``. Only the brand owner can close it, and only in one of
two ways: "it was sent" (they found it) or "cancel" (close it with the outcome
still unknown). Neither ever sends anything again.

Run: python manage.py test apps.growth_agent.tests.test_resolution --settings=config.settings.test
"""

from apps.accounts.models import Subscription

from ..models import AgentActionLog, ApprovalDecision, GrowthAction, GrowthProgram, GrowthTask
from ..services.control import approvals, resolution
from ..services.execution import executor
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_approval_flow import AGENCY_ADMIN, WRONG_HASH, ApprovalFlowTestCase


class UnknownOutcomeTestCase(ApprovalFlowTestCase):
    """An approved draft whose send crashed: the outcome is unknown."""

    def setUp(self):
        super().setUp()
        self.channel.send_crash = True
        self.approve()
        self.send()
        self.channel.send_crash = False
        self.assertEqual(self.action.state, GrowthAction.State.NEEDS_RECONCILIATION)

    def resolve(self, resolution_name: str, *, email: str = PRO_EMAIL, **body):
        self._body = {
            "resolution": resolution_name,
            "version": self.version.number,
            "payload_hash": self.version.payload_hash,
            **body,
        }
        return self.signed("POST", f"{self.base()}/actions/{self.action.public_id}/resolve/", email)


class ConfirmedSentTests(UnknownOutcomeTestCase):
    def test_owner_confirms_it_was_sent(self):
        response = self.resolve("confirmed_sent", reason="Found it in the Sent folder.")
        self.assertEqual(response.status_code, 200)
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.SUCCEEDED)
        self.assertEqual(self.task.status, GrowthTask.Status.DONE)
        self.assertEqual(response.json()["action"]["state"], "succeeded")

    def test_the_resolution_is_recorded_against_the_exact_version(self):
        self.resolve("confirmed_sent")
        record = ApprovalDecision.objects.filter(action=self.action).last()
        self.assertEqual(record.decision, ApprovalDecision.Decision.RESOLVED_SENT)
        self.assertEqual(record.version, self.version)
        self.assertEqual(record.payload_hash, self.version.payload_hash)
        self.assertEqual(record.actor_email, PRO_EMAIL)

    def test_a_confirmed_send_counts_against_the_daily_cap(self):
        before = AgentActionLog.objects.filter(action="publish", outcome="succeeded").count()
        self.resolve("confirmed_sent")
        after = AgentActionLog.objects.filter(action="publish", outcome="succeeded")
        self.assertEqual(after.count(), before + 1)
        self.assertEqual(after.last().actor_email, PRO_EMAIL)

    def test_confirming_sends_nothing(self):
        self.resolve("confirmed_sent")
        self.assertEqual(len(self.channel.executed), 1)


class CancelTests(UnknownOutcomeTestCase):
    def test_owner_closes_it_with_the_outcome_unknown(self):
        response = self.resolve("cancel", reason="Could not check the mailbox.")
        self.assertEqual(response.status_code, 200)
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.CANCELLED)
        self.assertEqual(self.task.status, GrowthTask.Status.SKIPPED)
        self.assertIn("outcome unknown", self.task.result_payload["skipped"])

    def test_cancel_is_recorded_and_audited_with_who_did_it(self):
        self.resolve("cancel")
        record = ApprovalDecision.objects.filter(action=self.action).last()
        self.assertEqual(record.decision, ApprovalDecision.Decision.RESOLVED_CANCELLED)
        entry = AgentActionLog.objects.filter(action="outcome_closed").get()
        self.assertEqual(entry.actor_email, PRO_EMAIL)

    def test_cancelling_sends_nothing_and_is_never_picked_up_again(self):
        self.resolve("cancel")
        self.send()
        self.assertEqual(len(self.channel.executed), 1)
        self.assertEqual(self.action.state, GrowthAction.State.CANCELLED)


class RefusalTests(UnknownOutcomeTestCase):
    def test_a_resend_is_not_an_option(self):
        response = self.resolve("authorize_resend")
        self.assertEqual(response.status_code, 400)
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.NEEDS_RECONCILIATION)

    def test_a_stale_version_or_hash_is_refused(self):
        response = self.resolve("confirmed_sent", payload_hash=WRONG_HASH)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "stale")
        response = self.resolve("cancel", version=self.version.number + 1)
        self.assertEqual(response.json()["code"], "stale")
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.NEEDS_RECONCILIATION)

    def test_the_request_must_name_the_resolution_version_and_hash(self):
        self._body = {}
        path = f"{self.base()}/actions/{self.action.public_id}/resolve/"
        self.assertEqual(self.signed("POST", path, PRO_EMAIL).status_code, 400)

    def test_repeating_the_same_resolution_records_it_once(self):
        self.resolve("confirmed_sent")
        self.assertEqual(self.resolve("confirmed_sent").status_code, 200)
        resolved = ApprovalDecision.objects.filter(
            action=self.action, decision=ApprovalDecision.Decision.RESOLVED_SENT
        )
        self.assertEqual(resolved.count(), 1)

    def test_a_resolved_action_cannot_be_resolved_the_other_way(self):
        self.resolve("confirmed_sent")
        response = self.resolve("cancel")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "illegal_state")

    def test_only_an_unknown_outcome_can_be_resolved(self):
        task = self.make_task(title="Another page")
        executor.run_task(task.id)
        pending = GrowthAction.objects.select_related("current_version").get(task=task)
        version = pending.current_version
        self._body = {
            "resolution": "confirmed_sent",
            "version": version.number,
            "payload_hash": version.payload_hash,
        }
        response = self.signed("POST", f"{self.base()}/actions/{pending.public_id}/resolve/", PRO_EMAIL)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "illegal_state")

    def test_resolving_does_not_restore_a_withdrawn_approval(self):
        self.resolve("cancel")
        self.action.refresh_from_db()
        self.assertFalse(approvals.has_valid_approval(self.action))


class ResolutionAuthorizationTests(UnknownOutcomeTestCase):
    def setUp(self):
        super().setUp()
        # A paying owner of another brand, so the plan check passes and only
        # brand ownership decides.
        Subscription.objects.create(email=OTHER_EMAIL, plan="pro", status="active")
        GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)

    def assert_unresolved(self):
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.NEEDS_RECONCILIATION)

    def test_another_brands_owner_gets_not_found(self):
        response = self.resolve("confirmed_sent", email=OTHER_EMAIL)
        self.assertEqual(response.status_code, 404)
        self.assert_unresolved()

    def test_another_brands_owner_cannot_reach_it_through_their_own_brand(self):
        self._body = {
            "resolution": "cancel",
            "version": self.version.number,
            "payload_hash": self.version.payload_hash,
        }
        path = f"{self.base(self.other_org.slug)}/actions/{self.action.public_id}/resolve/"
        self.assertEqual(self.signed("POST", path, OTHER_EMAIL).status_code, 404)
        self.assert_unresolved()

    def test_an_agency_admin_cannot_resolve(self):
        response = self.resolve("cancel", email=AGENCY_ADMIN)
        self.assertIn(response.status_code, (403, 404))
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.NEEDS_RECONCILIATION)

    def test_the_service_refuses_another_programs_action(self):
        other = GrowthProgram.objects.get(organization=self.other_org)
        request = resolution.ResolutionRequest(
            resolution="cancel",
            version_number=self.version.number,
            payload_hash=self.version.payload_hash,
            actor_email=OTHER_EMAIL,
        )
        with self.assertRaises(approvals.ActionNotFound):
            resolution.resolve(other, self.action.public_id, request)

    def test_the_detail_shows_the_resolution_without_the_owner_address(self):
        self.resolve("confirmed_sent", reason="Seen in Sent.")
        body = self.signed("GET", f"{self.base()}/actions/{self.action.public_id}/", PRO_EMAIL).json()
        decisions = [d["decision"] for d in body["action"]["decisions"]]
        self.assertEqual(decisions, ["approved", "resolved_sent"])
        self.assertNotIn(PRO_EMAIL, str(body))
