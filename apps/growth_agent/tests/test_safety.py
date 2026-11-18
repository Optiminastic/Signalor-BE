"""
Safety changes: a high-risk change from an unknown source blocks approvals and
sends until reviewed; the owner confirms a connector change with an emailed
code; ownership changes are superuser-only.

The database triggers that write change rows only exist on Postgres
(test_safety_pg); here change rows are created directly.

Run: python manage.py test apps.growth_agent.tests.test_safety --settings=config.settings.test
"""

import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import override_settings

from ..models import (
    ApprovalDecision,
    GrowthAction,
    GrowthSafetyChange,
    GrowthSafetyReview,
    SafetyConfirmationEvent,
)
from ..services.control import confirmations, safety, step_up
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_approval_flow import ApprovalFlowTestCase

KEY = "k" * 40
OLD_SITE, NEW_SITE = "https://old.example", "https://new.example"


@override_settings(GROWTH_CONFIRMATION_HMAC_KEY=KEY)
class SafetyTestCase(ApprovalFlowTestCase):
    def change(
        self, field="metadata.site_url", actor="unknown", old=OLD_SITE, new=NEW_SITE, entity="integration"
    ):
        return GrowthSafetyChange.objects.create(
            organization=self.pro_org,
            entity=entity,
            entity_id=1,
            provider="wordpress",
            field=field,
            old_digest=safety.digest(old),
            new_digest=safety.digest(new),
            old_display=old,
            new_display=new,
            actor=actor,
        )

    def url(self, change, verb=""):
        return (
            f"{self.base()}/safety-changes/{change.id}/{verb}" if verb else f"{self.base()}/safety-changes/"
        )

    def challenge(self, change, email=PRO_EMAIL):
        with self.captureOnCommitCallbacks(execute=True):
            return self.signed("POST", self.url(change, "challenge/"), email)

    def emailed_code(self) -> str:
        return re.search(r"code: (\d{8})", mail.outbox[-1].body).group(1)

    def confirm(self, change, code, old=OLD_SITE, new=NEW_SITE, email=PRO_EMAIL, reason="Moved our blog"):
        self._body = {"code": code, "old_identity": old, "new_identity": new, "reason": reason}
        return self.signed("POST", self.url(change, "confirm/"), email)


class BlockingTests(SafetyTestCase):
    def test_an_unknown_source_change_blocks_approval(self):
        self.change()
        response = self.approve()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "safety_review_pending")
        self.assertFalse(ApprovalDecision.objects.exists())

    def test_rejecting_still_works_while_blocked(self):
        self.change()
        self.assertEqual(self.reject().status_code, 200)

    def test_an_approved_action_waits_instead_of_sending(self):
        self.approve()
        self.change()
        self.send()
        self.assertEqual(self.channel.executed, [])
        self.assertEqual(self.action.state, GrowthAction.State.APPROVED)

    def test_attributed_and_low_risk_changes_do_not_block(self):
        self.change(actor="owner:abc")
        self.change(field="is_active", old="True", new="False")
        self.assertFalse(safety.blocked(self.pro_org.pk))
        self.assertEqual(self.approve().status_code, 200)

    def test_a_reversed_change_still_blocks(self):
        self.change(old=OLD_SITE, new=NEW_SITE)
        self.change(old=NEW_SITE, new=OLD_SITE)
        self.assertTrue(safety.blocked(self.pro_org.pk))

    def test_review_lifecycle(self):
        change = self.change()
        record = lambda outcome: safety.record_review(  # noqa: E731
            change, outcome, step_up=GrowthSafetyReview.StepUp.TOTP, reason="checked", reviewer_user_id=1
        )
        record("cleared")
        self.assertFalse(safety.blocked(self.pro_org.pk))
        record("confirmed_incident")
        self.assertTrue(safety.blocked(self.pro_org.pk))
        with self.assertRaises(safety.ReviewRefused):
            record("cleared")
        record("incident_resolved")
        self.assertFalse(safety.blocked(self.pro_org.pk))
        with self.assertRaises(safety.ReviewRefused):
            record("confirmed_incident")
        self.assertEqual(change.reviews.count(), 3)


class OwnerConfirmationTests(SafetyTestCase):
    def test_list_shows_the_change_to_the_owner_only(self):
        change = self.change()
        body = self.signed("GET", self.url(change), PRO_EMAIL).json()
        self.assertTrue(body["blocked"])
        self.assertEqual(body["changes"][0]["new"], NEW_SITE)
        self.assertIn(self.signed("GET", self.url(change), OTHER_EMAIL).status_code, (403, 404))

    def test_correct_code_and_identities_clear_the_change(self):
        change = self.change()
        self.assertEqual(self.challenge(change).status_code, 201)
        self.assertEqual(mail.outbox[-1].to, [PRO_EMAIL])
        response = self.confirm(change, self.emailed_code())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["blocked"])
        review = change.reviews.get()
        self.assertEqual((review.outcome, review.step_up), ("cleared", "email_code"))
        self.assertEqual(self.approve().status_code, 200)

    def test_wrong_identity_with_the_right_code_is_refused_and_spends_an_attempt(self):
        change = self.change()
        self.challenge(change)
        response = self.confirm(change, self.emailed_code(), new="https://other.example")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["attempts_left"], 4)
        self.assertTrue(safety.blocked(self.pro_org.pk))

    def test_five_attempts_then_the_code_is_dead(self):
        change = self.change()
        self.challenge(change)
        code = self.emailed_code()
        wrong = f"{(int(code) + 1) % 10**8:08d}"
        for _ in range(5):
            self.assertEqual(self.confirm(change, wrong).status_code, 400)
        self.assertEqual(self.confirm(change, code).status_code, 400)
        self.assertEqual(SafetyConfirmationEvent.objects.filter(event="attempt").count(), 5)
        self.assertTrue(safety.blocked(self.pro_org.pk))

    def test_a_new_code_supersedes_the_old_one(self):
        change = self.change()
        self.challenge(change)
        first = self.emailed_code()
        self.challenge(change)
        self.assertEqual(self.confirm(change, first).status_code, 400)
        self.assertEqual(self.confirm(change, self.emailed_code()).status_code, 200)

    def test_three_codes_an_hour(self):
        change = self.change()
        for _ in range(3):
            self.assertEqual(self.challenge(change).status_code, 201)
        self.assertEqual(self.challenge(change).status_code, 429)

    def test_expired_code_is_refused(self):
        change = self.change()
        self.challenge(change)
        code = self.emailed_code()
        later = SafetyConfirmationEvent.objects.get(event="issued").expires_at + timedelta(seconds=1)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.assertEqual(self.confirm(change, code).status_code, 400)
        self.assertTrue(safety.blocked(self.pro_org.pk))

    def test_ownership_changes_cannot_be_confirmed_by_the_owner(self):
        change = self.change(field="owner_email", entity="organization", old="a@x.example", new=PRO_EMAIL)
        self.assertEqual(self.challenge(change).status_code, 404)
        self.assertEqual(mail.outbox, [])

    def test_connector_confirmation_waits_while_ownership_is_in_doubt(self):
        change = self.change()
        self.change(field="owner_email", entity="organization", old="a@x.example", new=PRO_EMAIL)
        self.assertEqual(self.challenge(change).status_code, 409)

    def test_missing_key_refuses_to_issue_codes(self):
        change = self.change()
        with override_settings(GROWTH_CONFIRMATION_HMAC_KEY=""):
            self.assertEqual(self.challenge(change).status_code, 503)
        self.assertFalse(SafetyConfirmationEvent.objects.exists())

    def test_stored_verifier_is_keyed_and_never_the_code(self):
        change = self.change()
        self.challenge(change)
        code = self.emailed_code()
        issued = SafetyConfirmationEvent.objects.get(event="issued")
        self.assertNotIn(code, issued.verifier)
        other = confirmations._verifier(b"x" * 40, issued.challenge_id, change, issued.owner_digest, code)
        self.assertNotEqual(other, issued.verifier)

    def test_totp_codes_are_not_accepted_in_the_owner_flow_and_vice_versa(self):
        change = self.change()
        self.challenge(change)
        emailed = self.emailed_code()
        with self.assertRaises(step_up.StepUpRefused):
            step_up.verify_superuser("nobody", "x", emailed)
        self.assertEqual(self.confirm(change, "123456").status_code, 400)  # 6 digits: TOTP-shaped
