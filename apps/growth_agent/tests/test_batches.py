"""
Review batches: drafts grouped by day; bulk reject decides each item on its own;
bulk approve is refused (every approval is one decision on one exact draft).

Run: python manage.py test apps.growth_agent.tests.test_batches --settings=config.settings.test
"""

from django.utils import timezone

from ..models import ApprovalBatch, ApprovalBatchItem, ApprovalDecision, GrowthAction, GrowthTask
from ..services.control import editing
from ..services.execution import executor
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_approval_flow import ApprovalFlowTestCase


class BatchTests(ApprovalFlowTestCase):
    def second_draft(self) -> GrowthAction:
        task = GrowthTask.objects.create(
            cycle=self.cycle,
            week=1,
            scheduled_for=timezone.now().date(),
            channel="owned_content",
            tactic="answer_content",
            title="Second page",
            target="x",
        )
        executor.run_task(task.id)
        return GrowthAction.objects.select_related("current_version").get(task=task)

    def batch(self) -> ApprovalBatch:
        return ApprovalBatch.objects.get(organization=self.pro_org)

    def item(self, action, version=None) -> dict:
        version = version or action.current_version
        return {
            "action_id": action.public_id,
            "version": version.number,
            "payload_hash": version.payload_hash,
        }

    def decide_batch(self, decision, items, email=PRO_EMAIL, batch_id=None):
        self._body = {"decision": decision, "items": items}
        return self.signed("POST", f"{self.base()}/batches/{batch_id or self.batch().id}/decide/", email)

    def test_drafts_of_one_day_share_one_batch_and_pending_drafts_name_it(self):
        second = self.second_draft()
        batch = self.batch()
        self.assertEqual(set(batch.items.values_list("action_id", flat=True)), {self.action.id, second.id})
        listed = self.signed("GET", f"{self.base()}/approvals/", PRO_EMAIL).json()["actions"]
        self.assertTrue(all(a["batch_id"] == batch.id for a in listed))

    def test_bulk_reject_decides_each_item_independently(self):
        second = self.second_draft()
        self.approve()  # still rejectable: approved, not sent
        stale = {**self.item(second), "payload_hash": "0" * 64}
        response = self.decide_batch("rejected", [self.item(self.action), stale])
        self.assertEqual(response.status_code, 200)
        results = [r["result"] for r in response.json()["results"]]
        self.assertEqual(results, ["rejected", "stale"])
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.REJECTED)
        second.refresh_from_db()
        self.assertEqual(second.state, GrowthAction.State.AWAITING_APPROVAL)
        self.assertEqual(ApprovalDecision.objects.filter(decision="rejected").count(), 1)

    def test_bulk_approve_is_refused(self):
        response = self.decide_batch("approved", [self.item(self.action)])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "requires_individual")
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.AWAITING_APPROVAL)
        self.assertFalse(ApprovalDecision.objects.exists())

    def test_an_edit_adds_a_new_item_and_the_old_version_is_superseded(self):
        old = self.item(self.action)
        editing.edit(
            self.program,
            self.action.public_id,
            editing.EditRequest(
                self.version.number,
                self.version.payload_hash,
                {
                    "title": "A new title",
                    "meta_description": "New.",
                    "slug": "a-new-title",
                    "answer": "A new answer.",
                },
                PRO_EMAIL,
            ),
        )
        self.reload()
        self.assertEqual(ApprovalBatchItem.objects.filter(action=self.action).count(), 2)
        results = self.decide_batch("rejected", [old]).json()["results"]
        self.assertEqual(results[0]["result"], "superseded")
        self.assertEqual(self.action.state, GrowthAction.State.AWAITING_APPROVAL)

    def test_sent_or_unknown_items_are_not_found_or_illegal(self):
        self.approve()
        self.send()
        results = self.decide_batch(
            "rejected", [self.item(self.action), {**self.item(self.action), "action_id": "nope"}]
        )
        self.assertEqual([r["result"] for r in results.json()["results"]], ["illegal_state", "not_found"])

    def test_another_brands_owner_gets_404(self):
        self.assertIn(
            self.decide_batch("rejected", [self.item(self.action)], email=OTHER_EMAIL).status_code, (403, 404)
        )
        self.assertIn(self.signed("GET", f"{self.base()}/batches/", OTHER_EMAIL).status_code, (403, 404))

    def test_batch_listing_shows_items_with_current_flag(self):
        body = self.signed("GET", f"{self.base()}/batches/", PRO_EMAIL).json()
        item = body["batches"][0]["items"][0]
        self.assertEqual((item["action_id"], item["current"]), (self.action.public_id, True))
