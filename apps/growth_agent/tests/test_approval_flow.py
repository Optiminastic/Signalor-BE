"""
The approval flow, end to end on the backend:

    due task -> draft action -> the owner approves or rejects that exact draft
    -> only an approved draft can be sent, once.

Approving and sending are separate steps. These tests never touch a network:
the channel is a fake that records what it was asked to send.

Run: python manage.py test apps.growth_agent.tests.test_approval_flow --settings=config.settings.test
"""

from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import AccountProfile, AgencyMembership, Subscription
from apps.organizations.models import Organization

from ..models import (
    ActionVersion,
    AgentActionLog,
    ApprovalDecision,
    GrowthAction,
    GrowthProgram,
    GrowthTask,
    ImmutableRecordError,
)
from ..services.control import actions, approvals, dispatch
from ..services.execution import executor, guardrails
from ..services.execution.channel_base import ChannelOutcome
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_execution_flow import REGISTRY, ExecutionTestCase, FakeChannel

AGENCY_ADMIN = "admin@agency.example"
WRONG_HASH = "0" * 64


class ApprovalFlowTestCase(ExecutionTestCase):
    """A due task that has been prepared into a draft action."""

    def setUp(self):
        super().setUp()
        self.channel = FakeChannel()
        self.patcher = mock.patch.dict(REGISTRY, {"owned_content": self.channel})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        executor.run_task(self.task.id)
        self.task.refresh_from_db()
        self.action = GrowthAction.objects.select_related("current_version").get(task=self.task)
        self.version = self.action.current_version

    def base(self, slug: str | None = None) -> str:
        return f"/api/growth/programs/{slug or self.pro_org.slug}"

    def decide(self, verb: str, *, email: str = PRO_EMAIL, action=None, **body):
        action = action or self.action
        self._body = {"version": self.version.number, "payload_hash": self.version.payload_hash, **body}
        return self.signed("POST", f"{self.base()}/actions/{action.public_id}/{verb}/", email)

    def approve(self, **kwargs):
        return self.decide("approve", **kwargs)

    def reject(self, **kwargs):
        return self.decide("reject", **kwargs)

    def reload(self):
        self.action.refresh_from_db()
        self.task.refresh_from_db()

    def send(self):
        dispatch.dispatch(self.action.id)
        self.reload()


class DraftTests(ApprovalFlowTestCase):
    def test_a_draft_is_persisted_with_a_hash_and_waits_for_the_owner(self):
        self.assertEqual(self.action.state, GrowthAction.State.AWAITING_APPROVAL)
        self.assertEqual(self.task.status, GrowthTask.Status.AWAITING_APPROVAL)
        self.assertEqual(self.version.number, 1)
        self.assertEqual(len(self.version.payload_hash), 64)
        self.assertTrue(actions.version_is_intact(self.action, self.version))
        self.assertEqual(self.channel.executed, [])

    def test_hash_depends_on_content_and_on_destination(self):
        base = actions.content_hash("k", "c", {"a": 1}, {"to": "x"})
        self.assertEqual(base, actions.content_hash("k", "c", {"a": 1}, {"to": "x"}))
        self.assertNotEqual(base, actions.content_hash("k", "c", {"a": 2}, {"to": "x"}))
        self.assertNotEqual(base, actions.content_hash("k", "c", {"a": 1}, {"to": "y"}))
        self.assertNotEqual(base, actions.content_hash("k", "other", {"a": 1}, {"to": "x"}))

    def test_hash_does_not_depend_on_key_order(self):
        one = actions.content_hash("k", "c", {"a": 1, "b": 2}, {"x": 1, "y": 2})
        two = actions.content_hash("k", "c", {"b": 2, "a": 1}, {"y": 2, "x": 1})
        self.assertEqual(one, two)


class ApprovalStatusApiTests(ApprovalFlowTestCase):
    def test_owner_sees_the_pending_draft_with_its_exact_content(self):
        response = self.signed("GET", f"{self.base()}/approvals/", PRO_EMAIL)
        self.assertEqual(response.status_code, 200)
        (item,) = response.json()["actions"]
        self.assertEqual(item["id"], self.action.public_id)
        self.assertEqual(item["state"], "awaiting_approval")
        self.assertEqual(item["version"], 1)
        self.assertEqual(item["payload_hash"], self.version.payload_hash)
        self.assertEqual(item["content"]["title"], "Draft page")
        self.assertEqual(item["task"]["id"], self.task.id)

    def test_action_detail_includes_the_decision_history(self):
        self.approve()
        response = self.signed("GET", f"{self.base()}/actions/{self.action.public_id}/", PRO_EMAIL)
        body = response.json()["action"]
        self.assertEqual(body["state"], "approved")
        self.assertEqual([d["decision"] for d in body["decisions"]], ["approved"])
        self.assertNotIn(PRO_EMAIL, str(body))

    def test_plan_shows_the_action_on_its_task_and_keeps_task_status_values(self):
        response = self.signed("GET", f"{self.base()}/plan/", PRO_EMAIL)
        (task,) = response.json()["plan"]["tasks"]
        self.assertEqual(task["status"], "awaiting_approval")
        self.assertEqual(task["action"]["id"], self.action.public_id)
        self.assertEqual(task["action"]["state"], "awaiting_approval")

    def plan_query_count(self) -> int:
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            self.signed("GET", f"{self.base()}/plan/", PRO_EMAIL)
        return len(queries)

    def test_plan_query_count_does_not_grow_with_the_number_of_tasks(self):
        with_one_task = self.plan_query_count()
        for _ in range(3):
            executor.run_task(self.make_task().id)
        self.assertEqual(GrowthAction.objects.count(), 4)
        self.assertEqual(self.plan_query_count(), with_one_task)

    def test_a_decided_and_sent_action_leaves_the_pending_list(self):
        self.approve()
        self.send()
        response = self.signed("GET", f"{self.base()}/approvals/", PRO_EMAIL)
        self.assertEqual(response.json()["actions"], [])


class DecisionTests(ApprovalFlowTestCase):
    def test_owner_approves_and_nothing_is_sent_by_approving(self):
        response = self.approve()
        self.assertEqual(response.status_code, 200)
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.APPROVED)
        self.assertEqual(self.task.status, GrowthTask.Status.AWAITING_APPROVAL)
        self.assertEqual(self.channel.executed, [])

    def test_approval_is_recorded_against_the_exact_version(self):
        self.approve(reason="Looks right")
        decision = ApprovalDecision.objects.get()
        self.assertEqual(decision.decision, ApprovalDecision.Decision.APPROVED)
        self.assertEqual(decision.version, self.version)
        self.assertEqual(decision.payload_hash, self.version.payload_hash)
        self.assertEqual((decision.actor_email, decision.actor_role), (PRO_EMAIL, "owner"))
        self.assertEqual(decision.reason, "Looks right")
        self.assertEqual(decision.organization, self.pro_org)

    def test_owner_rejects_and_the_task_is_skipped(self):
        response = self.reject(reason="Not our tone")
        self.assertEqual(response.status_code, 200)
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.REJECTED)
        self.assertEqual(self.task.status, GrowthTask.Status.SKIPPED)
        self.assertIn("Rejected", self.task.result_payload["skipped"])

    def test_an_approval_can_be_withdrawn_by_rejecting_before_it_is_sent(self):
        self.approve()
        self.assertEqual(self.reject().status_code, 200)
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.REJECTED)
        self.assertEqual(
            list(ApprovalDecision.objects.values_list("decision", flat=True)), ["approved", "rejected"]
        )

    def test_a_rejected_draft_cannot_be_approved_afterwards(self):
        self.reject()
        response = self.approve()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "illegal_state")

    def test_a_stale_version_or_hash_is_refused(self):
        for body in ({"version": 2}, {"payload_hash": WRONG_HASH}):
            with self.subTest(body=body):
                response = self.approve(**body)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["code"], "stale")
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.AWAITING_APPROVAL)
        self.assertFalse(ApprovalDecision.objects.exists())

    def test_the_request_must_name_the_version_and_hash(self):
        self._body = {}
        response = self.signed("POST", f"{self.base()}/actions/{self.action.public_id}/approve/", PRO_EMAIL)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ApprovalDecision.objects.exists())

    def test_repeating_the_same_decision_records_it_once(self):
        self.assertEqual(self.approve().status_code, 200)
        self.assertEqual(self.approve().status_code, 200)
        self.assertEqual(ApprovalDecision.objects.count(), 1)
        self.assertEqual(AgentActionLog.objects.filter(action="approved").count(), 1)

    def test_a_sent_action_can_no_longer_be_rejected(self):
        self.approve()
        self.send()
        response = self.reject()
        self.assertEqual(response.status_code, 409)
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.SUCCEEDED)


class AuthorizationTests(ApprovalFlowTestCase):
    def setUp(self):
        super().setUp()
        Subscription.objects.create(email=OTHER_EMAIL, plan="pro", status="active")
        GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)

    def assert_untouched(self):
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.AWAITING_APPROVAL)
        self.assertFalse(ApprovalDecision.objects.exists())

    def test_another_brands_owner_cannot_decide_through_this_brand(self):
        self.assertEqual(self.approve(email=OTHER_EMAIL).status_code, 404)
        self.assertEqual(self.reject(email=OTHER_EMAIL).status_code, 404)
        self.assert_untouched()

    def test_another_brands_owner_cannot_reach_the_action_through_their_own_brand(self):
        self._body = {"version": self.version.number, "payload_hash": self.version.payload_hash}
        path = f"{self.base(self.other_org.slug)}/actions/{self.action.public_id}"
        self.assertEqual(self.signed("POST", f"{path}/approve/", OTHER_EMAIL).status_code, 404)
        self.assertEqual(self.signed("GET", f"{path}/", OTHER_EMAIL).status_code, 404)
        self.assert_untouched()

    def test_another_brands_owner_cannot_list_or_read(self):
        self.assertEqual(self.signed("GET", f"{self.base()}/approvals/", OTHER_EMAIL).status_code, 404)
        own = self.signed("GET", f"{self.base(self.other_org.slug)}/approvals/", OTHER_EMAIL)
        self.assertEqual(own.json()["actions"], [])

    def test_a_missing_action_and_a_foreign_action_answer_the_same(self):
        self._body = {"version": 1, "payload_hash": self.version.payload_hash}
        other = f"{self.base(self.other_org.slug)}/actions"
        foreign = self.signed("POST", f"{other}/{self.action.public_id}/approve/", OTHER_EMAIL)
        missing = self.signed("POST", f"{other}/doesnotexist/approve/", OTHER_EMAIL)
        self.assertEqual((foreign.status_code, foreign.json()), (missing.status_code, missing.json()))

    def test_an_unsigned_request_is_refused(self):
        response = self.client.post(
            f"{self.base()}/actions/{self.action.public_id}/approve/",
            data={"version": 1, "payload_hash": self.version.payload_hash},
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))
        self.assert_untouched()

    def test_an_agency_admin_cannot_decide(self):
        """Approval is for the brand owner only, whatever agency role the caller holds."""
        AccountProfile.objects.create(email=PRO_EMAIL, account_type="agency")
        AgencyMembership.objects.create(
            agency_email=PRO_EMAIL,
            member_email=AGENCY_ADMIN,
            role=AgencyMembership.Role.ADMIN,
            status=AgencyMembership.Status.ACTIVE,
        )
        Subscription.objects.create(email=AGENCY_ADMIN, plan="pro", status="active")
        self.assertEqual(self.approve(email=AGENCY_ADMIN).status_code, 404)
        self.assert_untouched()


class AuditTests(ApprovalFlowTestCase):
    def actions_logged(self) -> list[str]:
        return list(AgentActionLog.objects.order_by("created_at", "id").values_list("action", flat=True))

    def test_each_step_is_written_to_the_audit_history_in_order(self):
        self.approve()
        self.send()
        self.assertEqual(self.actions_logged(), ["prepared", "approved", "publish"])

    def test_the_decision_entry_names_who_decided(self):
        self.reject()
        entry = AgentActionLog.objects.get(action="rejected")
        self.assertEqual(entry.actor_email, PRO_EMAIL)
        self.assertEqual(entry.detail["action"], self.action.public_id)
        self.assertEqual(entry.detail["version"], 1)
        self.assertNotIn(PRO_EMAIL, str(entry.detail))

    def test_entries_the_agent_writes_on_its_own_have_no_actor(self):
        self.assertEqual(AgentActionLog.objects.get(action="prepared").actor_email, "")

    def test_the_activity_endpoint_does_not_expose_the_actor_address(self):
        self.approve()
        response = self.signed("GET", f"{self.base()}/activity/", PRO_EMAIL)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(PRO_EMAIL, str(response.json()))


class SendGateTests(ApprovalFlowTestCase):
    """Only an approved draft is sent, exactly as stored, and only once."""

    def test_an_unapproved_draft_is_never_sent(self):
        self.send()
        self.assertEqual(self.channel.executed, [])
        self.assertEqual(self.action.state, GrowthAction.State.AWAITING_APPROVAL)

    def test_a_rejected_draft_is_never_sent(self):
        self.reject()
        self.send()
        self.assertEqual(self.channel.executed, [])
        self.assertEqual(self.action.state, GrowthAction.State.REJECTED)

    def test_an_approved_draft_is_sent_exactly_as_stored(self):
        self.approve()
        self.send()
        self.assertEqual(self.channel.executed, [self.version])
        self.assertEqual(self.action.state, GrowthAction.State.SUCCEEDED)
        self.assertEqual(self.task.status, GrowthTask.Status.DONE)
        self.assertEqual(self.task.evidence_url, "https://pro.example/blog/x")

    def test_sending_twice_sends_once(self):
        self.approve()
        self.send()
        self.send()
        self.assertEqual(len(self.channel.executed), 1)
        self.assertEqual(AgentActionLog.objects.filter(action="publish").count(), 1)

    def test_an_approval_withdrawn_before_sending_stops_the_send(self):
        self.approve()
        self.reject()
        self.send()
        self.assertEqual(self.channel.executed, [])

    def test_state_set_to_approved_without_a_decision_is_refused(self):
        """Marking the row approved is not an approval. A decision record is."""
        GrowthAction.objects.filter(pk=self.action.pk).update(state=GrowthAction.State.APPROVED)
        self.send()
        self.assertEqual(self.channel.executed, [])
        self.assertEqual(self.action.state, GrowthAction.State.FAILED)
        self.assertTrue(AgentActionLog.objects.filter(action="dispatch_refused").exists())

    def test_content_changed_after_approval_is_detected_and_not_sent(self):
        self.approve()
        tampered = {**self.version.payload, "slug": "something-else"}
        ActionVersion._base_manager.filter(pk=self.version.pk).update(payload=tampered)
        self.send()
        self.assertEqual(self.channel.executed, [])
        self.assertEqual(self.action.state, GrowthAction.State.FAILED)
        self.assertEqual(self.task.status, GrowthTask.Status.FAILED)

    def test_destination_changed_after_approval_is_detected_and_not_sent(self):
        self.approve()
        ActionVersion._base_manager.filter(pk=self.version.pk).update(destination={"platform": "github"})
        self.send()
        self.assertEqual(self.channel.executed, [])
        self.assertEqual(self.action.state, GrowthAction.State.FAILED)

    def test_kill_switch_holds_an_approved_draft_without_losing_the_approval(self):
        self.approve()
        GrowthProgram.objects.filter(pk=self.program.pk).update(kill_switch=True)
        self.send()
        self.assertEqual(self.channel.executed, [])
        self.assertEqual(self.action.state, GrowthAction.State.APPROVED)
        GrowthProgram.objects.filter(pk=self.program.pk).update(kill_switch=False)
        self.send()
        self.assertEqual(len(self.channel.executed), 1)

    def test_daily_cap_holds_an_approved_draft_for_a_later_round(self):
        self.approve()
        for _ in range(guardrails.DAILY_CHANNEL_CAPS["owned_content"]):
            AgentActionLog.objects.create(
                program=self.program,
                channel="owned_content",
                action="publish",
                outcome=AgentActionLog.Outcome.SUCCEEDED,
            )
        self.send()
        self.assertEqual(self.channel.executed, [])
        self.assertEqual(self.action.state, GrowthAction.State.APPROVED)

    def test_only_approved_and_due_actions_are_picked_up_for_sending(self):
        today = timezone.now().date()
        self.assertEqual(list(dispatch.dispatchable(today)), [])
        self.approve()
        self.assertEqual(list(dispatch.dispatchable(today)), [self.action])
        GrowthProgram.objects.filter(pk=self.program.pk).update(status=GrowthProgram.Status.PAUSED)
        self.assertEqual(list(dispatch.dispatchable(today)), [])


class SendOutcomeTests(ApprovalFlowTestCase):
    def test_a_failure_before_anything_went_out_is_recorded_as_failed(self):
        self.channel.sent = ChannelOutcome(False, "Not connected.", external_call_made=False)
        self.approve()
        self.send()
        self.assertEqual(self.action.state, GrowthAction.State.FAILED)
        self.assertEqual(self.task.status, GrowthTask.Status.FAILED)
        self.assertEqual(self.task.result_payload["error"], "Not connected.")

    def test_a_failure_after_the_send_was_attempted_is_never_called_not_sent(self):
        self.channel.sent = ChannelOutcome(False, "Gmail /messages/send failed (HTTP 500)")
        self.approve()
        self.send()
        self.assertEqual(self.action.state, GrowthAction.State.NEEDS_RECONCILIATION)
        self.assertEqual(self.task.status, GrowthTask.Status.EXECUTING)
        self.assertTrue(AgentActionLog.objects.filter(action="outcome_unknown").exists())

    def test_a_crash_while_sending_leaves_the_outcome_unknown_and_is_not_retried(self):
        self.channel.send_crash = True
        self.approve()
        self.send()
        self.assertEqual(self.action.state, GrowthAction.State.NEEDS_RECONCILIATION)
        self.channel.send_crash = False
        self.send()
        self.assertEqual(len(self.channel.executed), 1)

    def test_an_unknown_outcome_is_not_put_back_for_re_preparation(self):
        self.channel.send_crash = True
        self.approve()
        self.send()
        later = timezone.now() + executor.STALE_EXECUTION + executor.STALE_EXECUTION
        self.assertEqual(executor.release_stale_executions(now=later), 0)
        self.assertEqual(GrowthAction.objects.filter(task=self.task).count(), 1)


class ImmutabilityTests(ApprovalFlowTestCase):
    def setUp(self):
        super().setUp()
        self.approve()
        self.decision = ApprovalDecision.objects.get()

    def test_a_version_cannot_be_changed_or_deleted(self):
        self.version.payload = {"changed": True}
        with self.assertRaises(ImmutableRecordError):
            self.version.save()
        with self.assertRaises(ImmutableRecordError):
            self.version.delete()
        with self.assertRaises(ImmutableRecordError):
            ActionVersion.objects.filter(pk=self.version.pk).update(payload={})
        with self.assertRaises(ImmutableRecordError):
            ActionVersion.objects.filter(pk=self.version.pk).delete()
        with self.assertRaises(ImmutableRecordError):
            self.action.versions.all().delete()

    def test_a_decision_cannot_be_changed_or_deleted(self):
        self.decision.decision = ApprovalDecision.Decision.REJECTED
        with self.assertRaises(ImmutableRecordError):
            self.decision.save()
        with self.assertRaises(ImmutableRecordError):
            self.decision.delete()
        with self.assertRaises(ImmutableRecordError):
            ApprovalDecision.objects.filter(pk=self.decision.pk).update(decision="rejected")
        with self.assertRaises(ImmutableRecordError):
            ApprovalDecision.objects.bulk_update([self.decision], ["decision"])
        with self.assertRaises(ImmutableRecordError):
            self.action.decisions.all().delete()

    def test_deleting_the_brand_still_removes_its_records(self):
        organization_id = self.pro_org.id
        Organization.objects.filter(pk=organization_id).delete()
        self.assertFalse(GrowthAction.objects.filter(organization_id=organization_id).exists())
        self.assertFalse(ActionVersion.objects.filter(pk=self.version.pk).exists())
        self.assertFalse(ApprovalDecision.objects.filter(pk=self.decision.pk).exists())

    def test_deleting_one_brand_leaves_another_brands_records(self):
        other_program = GrowthProgram.objects.create(
            organization=self.other_org, created_by_email=OTHER_EMAIL
        )
        self.assertIsNotNone(other_program)
        Organization.objects.filter(pk=self.other_org.pk).delete()
        self.assertTrue(ActionVersion.objects.filter(pk=self.version.pk).exists())
        self.assertTrue(ApprovalDecision.objects.filter(pk=self.decision.pk).exists())


class ServiceTests(ApprovalFlowTestCase):
    """The same rules hold when the service is called directly, not through the API."""

    def request(self, decision: str, **overrides) -> approvals.DecisionRequest:
        fields = {
            "decision": decision,
            "version_number": self.version.number,
            "payload_hash": self.version.payload_hash,
            "actor_email": PRO_EMAIL,
        }
        return approvals.DecisionRequest(**{**fields, **overrides})

    def test_row_locks_take_only_the_action_row(self):
        # Regression: locking the joined, nullable current_version made Postgres
        # refuse every approve, reject and send ("FOR UPDATE cannot be applied to
        # the nullable side of an outer join"). sqlite ignores FOR UPDATE, so this
        # pins the lock target; the Postgres suites will exercise it for real.
        self.assertEqual(actions.lock_actions().query.select_for_update_of, ("self",))

    def test_another_programs_action_is_not_found(self):
        other = GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)
        with self.assertRaises(approvals.ActionNotFound):
            approvals.decide(other, self.action.public_id, self.request("approved"))

    def test_has_valid_approval_follows_the_latest_decision(self):
        self.assertFalse(approvals.has_valid_approval(self.action))
        approvals.decide(self.program, self.action.public_id, self.request("approved"))
        self.action.refresh_from_db()
        self.assertTrue(approvals.has_valid_approval(self.action))
        approvals.decide(self.program, self.action.public_id, self.request("rejected"))
        self.action.refresh_from_db()
        self.assertFalse(approvals.has_valid_approval(self.action))
