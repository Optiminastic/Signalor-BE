"""
The agent doesn't refuse on its own; the owner decides.

- No published contact email on a site: the task waits for the owner, who
  can add an email (the agent then writes the pitch for it) or skip it.
- The fact-check has concerns about a pitch: the draft still reaches
  Approvals, with the concerns attached, for the owner to edit, approve or
  reject.

Run: python manage.py test apps.growth_agent.tests.test_owner_decides --settings=config.settings.test
"""

from unittest import mock

from ..models import AgentActionLog, GrowthAction, GrowthTask, OutreachContact
from ..services.execution import executor
from ..services.execution.safety import SafetyVerdict
from ..services.outreach import contacts, pitch
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_execution_flow import REGISTRY
from .test_outreach import CH, GOOD_BODY, OutreachTestCase

DRAFT = pitch.PitchDraft(subject="Your analytics roundup", body=GOOD_BODY)
ASYNC_RUN = "apps.growth_agent.views_control.run_growth_task"


class OwnerDecidesTestCase(OutreachTestCase):
    def run_prepare(self, *, contact=None, verdict=SafetyVerdict(True, ())):
        with (
            mock.patch.dict(REGISTRY, {"outreach_email": self.channel}),
            mock.patch(f"{CH}.discover_contact", return_value=contact),
            mock.patch(f"{CH}.brand_facts", return_value="facts"),
            mock.patch(f"{CH}.write_pitch", return_value=DRAFT),
            mock.patch(f"{CH}.check_pitch", return_value=verdict),
        ):
            executor.run_task(self.task.id)
        self.task.refresh_from_db()

    def contact_endpoint(self, body, email=PRO_EMAIL):
        self._body = body
        path = f"/api/growth/programs/{self.pro_org.slug}/tasks/{self.task.id}/contact/"
        with mock.patch(ASYNC_RUN) as run:
            response = self.signed("POST", path, email)
        return response, run


class NoContactTests(OwnerDecidesTestCase):
    def test_no_contact_waits_for_the_owner_instead_of_failing(self):
        self.run_prepare(contact=None)
        self.assertEqual(self.task.status, GrowthTask.Status.AWAITING_APPROVAL)
        self.assertEqual(self.task.result_payload["needs_contact"]["domain"], "site.com")
        self.assertFalse(GrowthAction.objects.filter(task=self.task).exists())
        entry = AgentActionLog.objects.filter(action="contact_not_found").get()
        self.assertEqual(entry.detail["domain"], "site.com")

    def test_a_waiting_task_is_not_retried_by_the_scheduler(self):
        self.run_prepare(contact=None)
        self.assertNotIn(self.task, list(executor.due_tasks(self.task.scheduled_for)))

    def test_the_owner_adds_an_email_and_the_agent_drafts_for_it(self):
        self.run_prepare(contact=None)
        response, run = self.contact_endpoint({"email": "Editor@Site.com"})
        self.assertEqual(response.status_code, 202)
        run.delay.assert_called_once_with(self.task.id)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, GrowthTask.Status.PLANNED)
        contact = OutreachContact.objects.get(organization=self.pro_org, email="editor@site.com")
        self.assertEqual(self.task.result_payload["owner_contact_id"], contact.id)
        # The queued run then drafts to that contact. Discovery would find nothing,
        # so a draft only exists if the owner's email was used.
        self.run_prepare(contact=None)
        action = GrowthAction.objects.select_related("current_version").get(task=self.task)
        self.assertEqual(action.current_version.destination["contact_id"], contact.id)
        self.assertTrue(AgentActionLog.objects.filter(action="contact_added", actor_email=PRO_EMAIL).exists())

    def test_the_owner_can_skip_it(self):
        self.run_prepare(contact=None)
        response, run = self.contact_endpoint({"skip": True})
        self.assertEqual(response.status_code, 200)
        run.delay.assert_not_called()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, GrowthTask.Status.SKIPPED)

    def test_an_invalid_email_or_an_opted_out_contact_is_refused(self):
        self.run_prepare(contact=None)
        self.assertEqual(self.contact_endpoint({"email": "not-an-email"})[0].status_code, 400)
        OutreachContact.objects.create(
            organization=self.pro_org, email="gone@site.com", domain="site.com", opted_out=True
        )
        self.assertEqual(self.contact_endpoint({"email": "gone@site.com"})[0].status_code, 409)

    def test_only_a_task_waiting_for_a_contact_accepts_one(self):
        response, _ = self.contact_endpoint({"email": "editor@site.com"})
        self.assertEqual(response.status_code, 409)

    def test_another_brands_owner_gets_not_found(self):
        self.run_prepare(contact=None)
        response, _ = self.contact_endpoint({"email": "editor@site.com"}, email=OTHER_EMAIL)
        self.assertIn(response.status_code, (403, 404))


class FactCheckConcernTests(OwnerDecidesTestCase):
    def test_a_flagged_pitch_still_reaches_approvals_with_its_concerns(self):
        verdict = SafetyVerdict(False, ("Claims 40% growth, which isn't in the brand facts.",))
        self.run_prepare(contact=contacts.Contact("editor@site.com", "https://site.com"), verdict=verdict)
        self.assertEqual(self.task.status, GrowthTask.Status.AWAITING_APPROVAL)
        action = GrowthAction.objects.select_related("current_version").get(task=self.task)
        self.assertEqual(action.state, GrowthAction.State.AWAITING_APPROVAL)
        self.assertEqual(action.current_version.review_notes, list(verdict.issues))
        self.assertTrue(AgentActionLog.objects.filter(action="drafted_with_concerns").exists())

    def test_the_review_shows_the_concerns(self):
        verdict = SafetyVerdict(False, ("Unverified claim.",))
        self.run_prepare(contact=contacts.Contact("editor@site.com", "https://site.com"), verdict=verdict)
        action = GrowthAction.objects.get(task=self.task)
        body = self.signed(
            "GET", f"/api/growth/programs/{self.pro_org.slug}/actions/{action.public_id}/", PRO_EMAIL
        )
        self.assertEqual(body.json()["action"]["content"]["review_notes"], ["Unverified claim."])

    def test_a_clean_pitch_has_no_concerns(self):
        self.run_prepare(contact=contacts.Contact("editor@site.com", "https://site.com"))
        action = GrowthAction.objects.select_related("current_version").get(task=self.task)
        self.assertEqual(action.current_version.review_notes, [])
        self.assertTrue(AgentActionLog.objects.filter(action="prepared").exists())
