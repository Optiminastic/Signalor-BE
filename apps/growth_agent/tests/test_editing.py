"""
Editing a draft before it is sent.

An edit never changes a version: it creates the next one, with the same
destination, and puts the action back in front of the owner. Any approval of
an earlier version no longer counts. Only content can change: an email's
subject and message (its sender and opt-out footer are kept exactly), an
article's title, description, slug and opening answer.

Run: python manage.py test apps.growth_agent.tests.test_editing --settings=config.settings.test
"""

from unittest import mock

from apps.accounts.models import Subscription

from ..models import ActionVersion, AgentActionLog, GrowthAction, GrowthProgram, GrowthTask
from ..services.control import actions, approvals, editing
from ..services.execution import executor
from ..services.execution.channel_base import Prepared
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_approval_flow import WRONG_HASH, ApprovalFlowTestCase
from .test_execution_flow import REGISTRY, FakeChannel

FOOTER = 'Pro Example (https://pro.example), sent by owner@pro.example\nNot relevant? Reply "no thanks".'
EMAIL_BODY = f"Hi there,\n\nWould you add Pro Example to your list?\n\n--\n{FOOTER}"


class EmailChannel(FakeChannel):
    def prepare(self, task):
        self.prepares += 1
        return Prepared(
            kind=GrowthAction.Kind.SEND_OUTREACH_EMAIL,
            connector="gmail",
            payload={"subject": "A suggestion", "body": EMAIL_BODY},
            destination={"contact_id": 1, "domain": "roundup.example", "sender": "owner@pro.example"},
            preview={},
        )


class EditTestCase(ApprovalFlowTestCase):
    def setUp(self):
        super().setUp()
        self.email_channel = EmailChannel()
        patcher = mock.patch.dict(REGISTRY, {"outreach_email": self.email_channel})
        patcher.start()
        self.addCleanup(patcher.stop)
        task = self.make_task(channel="outreach_email", tactic="listicle_inclusion", target="roundup.example")
        executor.run_task(task.id)
        self.email = GrowthAction.objects.select_related("current_version").get(task=task)

    def edit(self, action, fields, *, email=PRO_EMAIL, **overrides):
        version = action.current_version
        self._body = {
            "version": version.number,
            "payload_hash": version.payload_hash,
            "fields": fields,
            **overrides,
        }
        return self.signed("POST", f"{self.base()}/actions/{action.public_id}/edit/", email)

    def fresh(self, action):
        return GrowthAction.objects.select_related("current_version", "task").get(pk=action.pk)

    def article_fields(self, **overrides):
        return {
            "title": "A better title",
            "meta_description": "A sharper description.",
            "slug": "a-better-title",
            "answer": "The direct answer, rewritten.",
            **overrides,
        }


class EmailEditTests(EditTestCase):
    def test_editing_creates_the_next_version_and_keeps_the_footer(self):
        response = self.edit(self.email, {"subject": "New subject", "message": "Hello,\n\nShort and kind."})
        self.assertEqual(response.status_code, 200)
        action = self.fresh(self.email)
        self.assertEqual(action.current_version.number, 2)
        self.assertEqual(action.current_version.payload["subject"], "New subject")
        self.assertEqual(action.current_version.payload["body"], f"Hello,\n\nShort and kind.\n\n--\n{FOOTER}")
        self.assertEqual(action.current_version.destination, self.email.current_version.destination)
        self.assertTrue(actions.version_is_intact(action, action.current_version))

    def test_a_fake_footer_in_the_message_cannot_replace_the_real_one(self):
        self.edit(self.email, {"subject": "S", "message": "Hi\n\n--\nsomeone else"})
        body = self.fresh(self.email).current_version.payload["body"]
        self.assertTrue(body.endswith(f"\n\n--\n{FOOTER}"))

    def test_the_review_shows_the_message_and_footer_separately(self):
        response = self.signed("GET", f"{self.base()}/actions/{self.email.public_id}/", PRO_EMAIL)
        content = response.json()["action"]["content"]
        self.assertEqual(content["message"], "Hi there,\n\nWould you add Pro Example to your list?")
        self.assertEqual(content["footer"], FOOTER)
        self.assertEqual(content["body"], EMAIL_BODY)

    def test_the_old_version_is_kept_unchanged(self):
        original = self.email.current_version
        self.edit(self.email, {"subject": "New", "message": "New message"})
        original.refresh_from_db()
        self.assertEqual(original.payload["body"], EMAIL_BODY)
        self.assertEqual(ActionVersion.objects.filter(action=self.email).count(), 2)


class ArticleEditTests(EditTestCase):
    def test_editing_an_article_changes_only_the_editable_fields(self):
        before = self.action.current_version.payload["article"]
        response = self.edit(self.action, self.article_fields())
        self.assertEqual(response.status_code, 200)
        after = self.fresh(self.action).current_version.payload
        self.assertEqual(after["slug"], "a-better-title")
        self.assertEqual(after["article"]["title"], "A better title")
        self.assertEqual(after["article"]["answer"], "The direct answer, rewritten.")
        self.assertEqual(after["article"]["sections"], before["sections"])
        self.assertEqual(after["article"]["faq"], before["faq"])
        self.assertEqual(response.json()["action"]["content"]["answer"], "The direct answer, rewritten.")


class ApprovalAfterEditTests(EditTestCase):
    def test_an_edit_needs_a_fresh_approval_before_anything_is_sent(self):
        self.approve()
        self.edit(self.action, self.article_fields())
        action = self.fresh(self.action)
        self.assertEqual(action.state, GrowthAction.State.AWAITING_APPROVAL)
        self.assertEqual(action.task.status, GrowthTask.Status.AWAITING_APPROVAL)
        self.assertFalse(approvals.has_valid_approval(action))
        self.send()
        self.assertEqual(self.channel.executed, [])

    def test_the_new_version_is_sent_exactly_once_approved(self):
        self.edit(self.action, self.article_fields())
        action = self.fresh(self.action)
        version = action.current_version
        self._body = {"version": version.number, "payload_hash": version.payload_hash}
        self.signed("POST", f"{self.base()}/actions/{action.public_id}/approve/", PRO_EMAIL)
        self.send()
        (sent,) = self.channel.executed
        self.assertEqual(sent.number, 2)

    def test_approving_the_old_version_after_an_edit_is_refused_as_stale(self):
        old = self.action.current_version
        self.edit(self.action, self.article_fields())
        self._body = {"version": old.number, "payload_hash": old.payload_hash}
        response = self.signed("POST", f"{self.base()}/actions/{self.action.public_id}/approve/", PRO_EMAIL)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "stale")

    def test_a_rejected_draft_can_be_edited_back_into_review(self):
        self.reject()
        self.edit(self.action, self.article_fields())
        action = self.fresh(self.action)
        self.assertEqual(action.state, GrowthAction.State.AWAITING_APPROVAL)
        self.assertEqual(action.task.status, GrowthTask.Status.AWAITING_APPROVAL)
        self.assertNotIn("skipped", action.task.result_payload)


class EditRefusalTests(EditTestCase):
    def assert_refused(self, response, code):
        self.assertEqual(response.json().get("code"), code, response.json())

    def test_an_edit_of_an_outdated_version_is_refused_as_stale(self):
        stale = self.edit(self.email, {"subject": "S", "message": "M"}, payload_hash=WRONG_HASH)
        self.assertEqual(stale.status_code, 409)
        self.assert_refused(stale, "stale")
        self.assertEqual(self.fresh(self.email).current_version.number, 1)

    def test_unchanged_content_creates_no_new_version(self):
        fields = {
            "subject": "A suggestion",
            "message": "Hi there,\n\nWould you add Pro Example to your list?",
        }
        self.assertEqual(self.edit(self.email, fields).status_code, 200)
        self.assertEqual(self.fresh(self.email).current_version.number, 1)

    def test_the_destination_and_other_fields_cannot_be_edited(self):
        for extra in (
            {"recipient": "x@evil.example"},
            {"sender": "x@evil.example"},
            {"domain": "evil.example"},
        ):
            response = self.edit(self.email, {"subject": "S", "message": "M", **extra})
            self.assertEqual(response.status_code, 400)
            self.assert_refused(response, "invalid_edit")
        self.assertEqual(self.fresh(self.email).current_version.number, 1)

    def test_invalid_email_content_is_refused(self):
        for fields in (
            {"subject": "", "message": "M"},
            {"subject": "Two\nlines", "message": "M"},
            {"subject": "S", "message": "   "},
            {"subject": "S"},
        ):
            self.assertEqual(self.edit(self.email, fields).status_code, 400, fields)

    def test_invalid_article_content_is_refused(self):
        for overrides in (
            {"title": ""},
            {"meta_description": "x" * 161},
            {"slug": "Not A Slug"},
            {"slug": ""},
            {"answer": ""},
        ):
            self.assertEqual(
                self.edit(self.action, self.article_fields(**overrides)).status_code, 400, overrides
            )

    def test_an_action_being_sent_or_done_cannot_be_edited(self):
        self.approve()
        self.send()
        response = self.edit(self.action, self.article_fields())
        self.assertEqual(response.status_code, 409)
        self.assert_refused(response, "illegal_state")

    def test_an_unknown_outcome_cannot_be_edited(self):
        self.channel.send_crash = True
        self.approve()
        self.send()
        response = self.edit(self.action, self.article_fields())
        self.assert_refused(response, "illegal_state")

    def test_another_brands_owner_gets_not_found(self):
        Subscription.objects.create(email=OTHER_EMAIL, plan="pro", status="active")
        GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)
        response = self.edit(self.email, {"subject": "S", "message": "M"}, email=OTHER_EMAIL)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.fresh(self.email).current_version.number, 1)


class EditAuditTests(EditTestCase):
    def test_the_edit_is_audited_with_who_made_it(self):
        self.edit(self.email, {"subject": "New", "message": "New message"})
        entry = AgentActionLog.objects.filter(action="edited").get()
        self.assertEqual(entry.actor_email, PRO_EMAIL)
        self.assertEqual(entry.detail["version"], 2)
        self.assertEqual(entry.detail["from_version"], 1)

    def test_the_service_refuses_another_programs_action(self):
        other = GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)
        version = self.email.current_version
        request = editing.EditRequest(
            version_number=version.number,
            payload_hash=version.payload_hash,
            fields={"subject": "S", "message": "M"},
            actor_email=OTHER_EMAIL,
        )
        with self.assertRaises(approvals.ActionNotFound):
            editing.edit(other, self.email.public_id, request)
