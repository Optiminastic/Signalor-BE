"""
The "What happened" timeline on an action, and its send-attempt status.

One ordered list built from the stored records: the draft, each edit, each
decision, and each step of each send attempt. No email address is included.

Run: python manage.py test apps.growth_agent.tests.test_timeline --settings=config.settings.test
"""

from ..models import GrowthProgram
from ..services.control import dispatch, ledger
from .test_api import PRO_EMAIL
from .test_approval_flow import ApprovalFlowTestCase


class TimelineTests(ApprovalFlowTestCase):
    def detail(self) -> dict:
        response = self.signed("GET", f"{self.base()}/actions/{self.action.public_id}/", PRO_EMAIL)
        return response.json()["action"]

    def kinds(self, body: dict) -> list[str]:
        return [entry.get("decision") or entry.get("event") or entry["type"] for entry in body["timeline"]]

    def test_a_sent_action_shows_every_step_in_order(self):
        self.approve()
        self.send()
        body = self.detail()
        self.assertEqual(
            self.kinds(body), ["drafted", "approved", "claimed", "provider_started", "provider_result"]
        )
        result = body["timeline"][-1]
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["attempt"], 1)

    def test_a_send_held_back_before_claiming_adds_no_send_steps(self):
        self.approve()
        GrowthProgram.objects.filter(pk=self.program.pk).update(kill_switch=True)
        self.send()
        body = self.detail()
        self.assertEqual(self.kinds(body), ["drafted", "approved"])
        self.assertEqual(body["send_attempts"]["failures"], 0)

    def test_an_attempt_stopped_at_the_final_check_shows_why(self):
        self.approve()
        claimed = dispatch._claim(self.action.id)
        GrowthProgram.objects.filter(pk=self.program.pk).update(kill_switch=True)
        dispatch._gate(claimed)
        body = self.detail()
        self.assertEqual(self.kinds(body), ["drafted", "approved", "claimed", "aborted"])
        self.assertEqual(body["timeline"][-1]["reason"], "Kill switch is on.")

    def test_an_edit_appears_with_its_version(self):
        version = self.version
        article = version.payload["article"]
        self._body = {
            "version": version.number,
            "payload_hash": version.payload_hash,
            "fields": {
                "title": "New title",
                "meta_description": article["meta_description"],
                "slug": version.payload["slug"],
                "answer": article["answer"],
            },
        }
        self.signed("POST", f"{self.base()}/actions/{self.action.public_id}/edit/", PRO_EMAIL)
        body = self.detail()
        self.assertEqual(self.kinds(body), ["drafted", "edited"])
        self.assertEqual(body["timeline"][1]["version"], 2)

    def test_a_decision_entry_carries_its_reason_and_version_but_no_address(self):
        self.reject(reason="Off brand.")
        body = self.detail()
        entry = body["timeline"][1]
        self.assertEqual(entry["decision"], "rejected")
        self.assertEqual(entry["reason"], "Off brand.")
        self.assertEqual(entry["version"], 1)
        self.assertEqual(entry["actor_role"], "owner")
        self.assertNotIn(PRO_EMAIL, str(body))

    def test_a_blocked_action_says_so(self):
        self.approve()
        for seq in range(1, ledger.MAX_CONSECUTIVE_FAILURES + 1):
            ledger.write(self.action, ledger.Kind.ATTEMPT, seq)
            ledger.write(self.action, ledger.Kind.RESULT, seq, outcome=ledger.LOCK_CONFLICT)
        attempts = self.detail()["send_attempts"]
        self.assertTrue(attempts["blocked"])
        self.assertEqual(attempts["failures"], ledger.MAX_CONSECUTIVE_FAILURES)
        self.assertIsNone(attempts["retry_after"])

    def test_a_failure_shows_when_it_will_be_tried_again(self):
        self.approve()
        ledger.write(self.action, ledger.Kind.ATTEMPT, 1)
        attempts = self.detail()["send_attempts"]
        self.assertFalse(attempts["blocked"])
        self.assertEqual(attempts["failures"], 1)
        self.assertIsNotNone(attempts["retry_after"])
