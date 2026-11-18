"""
Outreach drafts don't need a connected mailbox; sending does.

The agent can find a contact and write the pitch for the owner to review
before Gmail is connected. An approved email then waits (it stays approved,
nothing fails) until a mailbox is connected.

Run: python manage.py test apps.growth_agent.tests.test_draft_without_mailbox --settings=config.settings.test
"""

from contextlib import ExitStack
from unittest import mock

from apps.integrations.models import Integration

from ..models import DispatchAttemptLedger, GrowthAction, GrowthTask
from ..services.control import actions, approvals, dispatch, ledger
from ..services.execution import executor
from ..services.execution.safety import SafetyVerdict
from ..services.outreach import contacts, pitch
from .test_api import PRO_EMAIL
from .test_execution_flow import REGISTRY
from .test_outreach import CH, OutreachTestCase


class DraftWithoutMailboxTests(OutreachTestCase):
    def setUp(self):
        super().setUp()
        Integration.objects.filter(pk=self.integration.pk).delete()

    def draft(self) -> GrowthAction:
        prepared, _ = self.prepare()
        return actions.create_action(self.task, prepared)

    def approve(self, action: GrowthAction) -> GrowthAction:
        version = action.current_version
        approvals.decide(
            self.program,
            action.public_id,
            approvals.DecisionRequest("approved", version.number, version.payload_hash, PRO_EMAIL),
        )
        return GrowthAction.objects.select_related("current_version").get(pk=action.pk)

    def test_drafting_needs_no_mailbox_but_sending_does(self):
        self.assertEqual(self.channel.readiness(self.program), "")
        self.assertIn("Connect Gmail", self.channel.send_blocker(self.program))

    def test_a_draft_is_prepared_without_a_mailbox(self):
        action = self.draft()
        version = action.current_version
        self.assertEqual(version.destination["sender"], "")
        self.assertIn("your connected mailbox", version.payload["body"])

    def test_the_scheduler_drafts_outreach_without_a_mailbox(self):
        with mock.patch.dict(REGISTRY, {"outreach_email": self.channel}), self.faked_preparation():
            executor.run_task(self.task.id)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, GrowthTask.Status.AWAITING_APPROVAL)
        self.assertTrue(GrowthAction.objects.filter(task=self.task).exists())

    def test_an_approved_email_waits_for_a_mailbox_and_nothing_fails(self):
        action = self.approve(self.draft())
        with mock.patch(f"{CH}.GmailClient") as client:
            dispatch.dispatch(action.id)
        client.return_value.send.assert_not_called()
        action.refresh_from_db()
        self.assertEqual(action.state, GrowthAction.State.APPROVED)
        result = DispatchAttemptLedger.objects.filter(action_public_id=action.public_id, kind="result").get()
        self.assertEqual(result.outcome, ledger.PREFLIGHT_WAIT)

    def test_a_draft_written_before_gmail_was_connected_is_refused_with_a_clear_reason(self):
        action = self.approve(self.draft())
        Integration.objects.create(
            organization=self.pro_org,
            provider=Integration.Provider.GMAIL,
            metadata={"email": "owner@brand-pro.com"},
        )
        with mock.patch(f"{CH}.GmailClient") as client:
            dispatch.dispatch(action.id)
        client.return_value.send.assert_not_called()
        action.refresh_from_db()
        self.assertEqual(action.state, GrowthAction.State.FAILED)
        self.task.refresh_from_db()
        self.assertIn("before Gmail was connected", self.task.result_payload["error"])

    def faked_preparation(self):
        stack = ExitStack()
        stack.enter_context(
            mock.patch(
                f"{CH}.discover_contact", return_value=contacts.Contact("editor@site.com", "https://site.com")
            )
        )
        stack.enter_context(mock.patch(f"{CH}.brand_facts", return_value="facts"))
        stack.enter_context(
            mock.patch(
                f"{CH}.write_pitch", return_value=pitch.PitchDraft(subject="Hello", body="A useful pitch.")
            )
        )
        stack.enter_context(mock.patch(f"{CH}.check_pitch", return_value=SafetyVerdict(True, ())))
        return stack
