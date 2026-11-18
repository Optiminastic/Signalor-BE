"""
A brand can't be deleted while one of its sends is in flight or its outcome is
unknown: both deletion endpoints answer 409 and nothing is deleted.

Run: python manage.py test apps.growth_agent.tests.test_safe_deletion --settings=config.settings.test
"""

from apps.analyzer.tests.auth_helpers import signed_in
from apps.organizations.models import Organization

from ..models import ActionExecutionEvent, GrowthAction, GrowthProgram
from ..services.control import resolution, trail
from .test_api import PRO_EMAIL
from .test_approval_flow import ApprovalFlowTestCase

ACCOUNT_DELETE_URL = "/api/account/delete/"


class SafeDeletionTests(ApprovalFlowTestCase):
    def delete_brand(self):
        with signed_in(PRO_EMAIL):
            return self.client.delete(f"/api/organizations/{self.pro_org.pk}/")

    def delete_account(self):
        with signed_in(PRO_EMAIL):
            return self.client.post(ACCOUNT_DELETE_URL, {"confirm": "delete my account"}, format="json")

    def in_flight(self):
        """Approved, claimed and past the final check: the provider call has started."""
        self.approve()
        GrowthAction.objects.filter(pk=self.action.pk).update(state=GrowthAction.State.DISPATCHING)
        self.action.refresh_from_db()
        trail.record(self.action, 1, ActionExecutionEvent.Event.CLAIMED)
        trail.record(self.action, 1, ActionExecutionEvent.Event.PROVIDER_STARTED)

    def test_a_send_in_flight_blocks_brand_deletion_with_409(self):
        self.in_flight()
        response = self.delete_brand()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "unresolved_executions")
        self.assertEqual(response.json()["in_flight"], 1)
        self.assertTrue(Organization.objects.filter(pk=self.pro_org.pk).exists())

    def test_an_unknown_outcome_blocks_until_the_owner_resolves_it(self):
        self.in_flight()
        trail.record(self.action, 1, ActionExecutionEvent.Event.PROVIDER_RESULT, outcome="unknown")
        GrowthAction.objects.filter(pk=self.action.pk).update(state=GrowthAction.State.NEEDS_RECONCILIATION)
        blocked = self.delete_brand()
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["awaiting_reconciliation"][0]["action"], self.action.public_id)
        self.action.refresh_from_db()
        resolution.resolve(
            self.program,
            self.action.public_id,
            resolution.ResolutionRequest("cancel", self.version.number, self.version.payload_hash, PRO_EMAIL),
        )
        self.assertEqual(self.delete_brand().status_code, 204)
        self.assertFalse(Organization.objects.filter(pk=self.pro_org.pk).exists())

    def test_account_deletion_is_all_or_nothing(self):
        self.in_flight()
        response = self.delete_account()
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Organization.objects.filter(pk=self.pro_org.pk).exists())
        self.assertTrue(GrowthProgram.objects.filter(pk=self.program.pk).exists())

    def test_a_claimed_but_not_started_send_does_not_block(self):
        self.approve()
        GrowthAction.objects.filter(pk=self.action.pk).update(state=GrowthAction.State.DISPATCHING)
        self.action.refresh_from_db()
        trail.record(self.action, 1, ActionExecutionEvent.Event.CLAIMED)
        self.assertEqual(self.delete_brand().status_code, 204)

    def test_a_brand_with_nothing_pending_deletes_as_before(self):
        self.assertEqual(self.delete_brand().status_code, 204)
