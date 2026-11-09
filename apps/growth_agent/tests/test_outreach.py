"""
Outreach: contact discovery, pitch rules, the email channel, follow-ups, Gmail connect.

Gmail, page fetching and the LLM are faked: nothing is fetched or sent.
Run: python manage.py test apps.growth_agent.tests.test_outreach --settings=config.settings.test
"""

from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.integrations.models import Integration

from ..models import AgentActionLog, GrowthTask, OutreachContact, OutreachThread
from ..services.execution import executor
from ..services.execution.safety import SafetyVerdict
from ..services.outreach import connect, contacts, followups, pitch
from ..services.outreach.channel import OutreachEmailChannel
from ..services.outreach.gmail import SentMessage, ThreadMessage
from .test_api import PRO_EMAIL
from .test_execution_flow import ExecutionTestCase

CH = "apps.growth_agent.services.outreach.channel"
GOOD_BODY = " ".join(["Your roundup of analytics tools is useful for our readers."] * 6)


class ContactDiscoveryTests(SimpleTestCase):
    def test_extract_filters_and_prefers_editorial_roles(self):
        page = (
            '<a href="mailto:noreply@site.com">x</a> <a href="mailto:jane@site.com">Jane</a> '
            "editor@site.com logo@2x.png someone@gmail.com privacy@site.com support@site.com"
        )
        self.assertEqual(contacts.extract_emails(page, "www.site.com"), ["editor@site.com", "jane@site.com"])

    def test_discover_falls_back_to_contact_page(self):
        pages = {"https://site.com/contact": '<a href="mailto:hello@site.com">mail us</a>'}
        found = contacts.discover_contact("https://site.com/best-tools", "site.com", fetch=pages.get)
        self.assertEqual(found, contacts.Contact("hello@site.com", "https://site.com/contact"))

    def test_never_guesses_when_nothing_is_published(self):
        self.assertIsNone(
            contacts.discover_contact("https://site.com/x", "site.com", fetch=lambda url: "<p>hi</p>")
        )


class PitchRuleTests(SimpleTestCase):
    def test_good_pitch_passes_rules(self):
        self.assertEqual(
            pitch.rule_issues(pitch.PitchDraft(subject="Your analytics roundup", body=GOOD_BODY)), []
        )

    def test_links_length_and_hype_are_flagged(self):
        issues = " ".join(
            pitch.rule_issues(pitch.PitchDraft(subject="Guaranteed growth", body="see https://x.com"))
        )
        self.assertIn("words", issues)
        self.assertIn("Links", issues)
        self.assertIn("guaranteed", issues)

    def test_footer_always_identifies_sender_and_offers_opt_out(self):
        body = pitch.with_footer("Hello", "me@brand.com", "Brand", "https://brand.com")
        self.assertIn("me@brand.com", body)
        self.assertIn(pitch.OPT_OUT_LINE, body)

    def test_opt_out_detection(self):
        self.assertTrue(pitch.is_opt_out("No thanks, please remove me"))
        self.assertFalse(pitch.is_opt_out("Sounds great, send details"))


class OutreachTestCase(ExecutionTestCase):
    def setUp(self):
        super().setUp()
        GrowthTask.objects.filter(pk=self.task.pk).update(
            channel="outreach_email",
            tactic="listicle_inclusion",
            target="site.com",
            target_url="https://site.com/best-tools",
            title="Get listed on site.com",
        )
        self.task.refresh_from_db()
        self.integration = Integration.objects.create(
            organization=self.pro_org,
            provider=Integration.Provider.GMAIL,
            metadata={"email": "owner@brand-pro.com"},
        )
        self.channel = OutreachEmailChannel()

    def run_channel(
        self, contact=contacts.Contact("editor@site.com", "https://site.com/best-tools"), mode="autonomous"
    ):
        draft = pitch.PitchDraft(subject="Your analytics roundup", body=GOOD_BODY)
        with (
            mock.patch(f"{CH}.discover_contact", return_value=contact),
            mock.patch(f"{CH}.brand_facts", return_value="facts"),
            mock.patch(f"{CH}.write_pitch", return_value=draft),
            mock.patch(f"{CH}.check_pitch", return_value=SafetyVerdict(True, ())),
            mock.patch(f"{CH}.GmailClient") as client,
        ):
            client.return_value.send.return_value = SentMessage("m1", "t1", "<id@mail>")
            outcome = self.channel.run(self.task, mode)
        return outcome, client


class OutreachChannelTests(OutreachTestCase):
    def test_sends_pitch_opens_thread_and_keeps_email_out_of_payload(self):
        outcome, client = self.run_channel()
        self.assertTrue(outcome.succeeded)
        self.assertNotIn("editor@site.com", str(outcome.payload))
        to, _subject, body = client.return_value.send.call_args.args
        self.assertEqual(to, "editor@site.com")
        self.assertIn(pitch.OPT_OUT_LINE, body)
        thread = OutreachThread.objects.get()
        self.assertEqual((thread.gmail_thread_id, thread.last_message_id), ("t1", "<id@mail>"))
        self.assertEqual(thread.next_follow_up_on, timezone.now().date() + timedelta(days=4))

    def test_no_contact_is_not_retried(self):
        outcome, client = self.run_channel(contact=None)
        self.assertFalse(outcome.succeeded)
        self.assertFalse(outcome.retryable)
        client.return_value.send.assert_not_called()

    def test_opted_out_and_recent_contacts_are_never_emailed(self):
        OutreachContact.objects.create(
            organization=self.pro_org, email="editor@site.com", domain="site.com", opted_out=True
        )
        outcome, client = self.run_channel()
        self.assertFalse(outcome.retryable)
        client.return_value.send.assert_not_called()
        OutreachContact.objects.update(opted_out=False, last_contacted_at=timezone.now() - timedelta(days=10))
        outcome, client = self.run_channel()
        self.assertIn("90 days", outcome.reason)
        client.return_value.send.assert_not_called()

    def test_dry_run_never_sends(self):
        outcome, client = self.run_channel(mode="dry_run")
        self.assertTrue(outcome.dry_run)
        client.return_value.send.assert_not_called()

    def test_not_ready_without_gmail(self):
        Integration.objects.all().delete()
        self.assertIn("Connect Gmail", self.channel.readiness(self.program))

    def test_executor_fails_non_retryable_task_immediately(self):
        with mock.patch(f"{CH}.discover_contact", return_value=None):
            executor.run_task(self.task.id)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, GrowthTask.Status.FAILED)
        self.assertEqual(self.task.attempts, 1)


class FollowUpTests(OutreachTestCase):
    def setUp(self):
        super().setUp()
        contact = OutreachContact.objects.create(
            organization=self.pro_org, email="editor@site.com", domain="site.com"
        )
        self.thread = OutreachThread.objects.create(
            task=self.task,
            contact=contact,
            gmail_thread_id="t1",
            last_message_id="<id@mail>",
            subject="Your analytics roundup",
            next_follow_up_on=timezone.now().date(),
        )
        self.client = mock.Mock()
        self.client.send.return_value = SentMessage("m2", "t1", "<id2@mail>")

    def process(self, messages):
        self.client.thread_messages.return_value = messages
        followups.process_thread(self.client, self.thread, "owner@brand-pro.com", timezone.now().date())
        self.thread.refresh_from_db()

    def test_reply_stops_follow_ups(self):
        self.process(
            [ThreadMessage("owner@brand-pro.com", "pitch"), ThreadMessage("editor@site.com", "Sure!")]
        )
        self.assertEqual(self.thread.status, OutreachThread.Status.REPLIED)
        self.client.send.assert_not_called()

    def test_opt_out_is_permanent(self):
        self.process([ThreadMessage("editor@site.com", "No thanks.")])
        self.assertEqual(self.thread.status, OutreachThread.Status.OPTED_OUT)
        self.assertTrue(self.thread.contact.opted_out)
        self.assertTrue(AgentActionLog.objects.filter(action="opt_out_received").exists())

    def test_due_follow_up_goes_in_thread_then_closes(self):
        self.process([ThreadMessage("owner@brand-pro.com", "pitch")])
        kwargs = self.client.send.call_args.kwargs
        self.assertEqual((kwargs["thread_id"], kwargs["in_reply_to"]), ("t1", "<id@mail>"))
        self.assertEqual(self.thread.followups_sent, 1)
        self.thread.next_follow_up_on = timezone.now().date()
        self.thread.save()
        self.process([])
        self.assertEqual(self.thread.followups_sent, 2)
        self.assertIsNone(self.thread.next_follow_up_on)
        self.process([])
        self.assertEqual(self.thread.status, OutreachThread.Status.CLOSED)

    def test_paused_programs_are_skipped(self):
        self.program.status = "paused"
        self.program.save()
        self.assertEqual(list(followups.open_threads()), [])


@override_settings(GROWTH_AGENT_APP_URL="https://agent.example")
class GmailConnectTests(OutreachTestCase):
    def test_state_roundtrip_and_tamper_rejection(self):
        url = connect.start(self.program, PRO_EMAIL)
        state = url.split("state=")[1]
        from urllib.parse import unquote

        self.assertEqual(connect._program_from_state(unquote(state)), self.program)
        with self.assertRaises(connect.ConnectError):
            connect._program_from_state(unquote(state) + "x")

    def test_finish_stores_tokens_and_sender(self):
        Integration.objects.all().delete()
        state = connect.signing.dumps(
            {"program": self.program.id, "email": PRO_EMAIL}, salt=connect.STATE_SALT
        )
        with (
            mock.patch.object(
                connect, "exchange_code", return_value={"access_token": "a", "refresh_token": "r"}
            ),
            mock.patch.object(connect, "GmailClient") as client,
        ):
            client.return_value.profile_email.return_value = "Owner@Brand-Pro.com"
            connect.finish("code", state)
        integration = Integration.objects.get(provider=Integration.Provider.GMAIL)
        self.assertEqual(integration.metadata["email"], "owner@brand-pro.com")
        self.assertEqual(integration.get_refresh_token(), "r")

    def test_callback_redirects_with_error_on_bad_state(self):
        response = self.client.get("/api/growth/oauth/gmail/callback/", {"code": "c", "state": "forged"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://agent.example/?connect_error=gmail_invalid_state")

    def test_connect_endpoint_scoped_to_owner(self):
        self._body = {}
        ok = self.signed("POST", f"/api/growth/programs/{self.pro_org.slug}/connections/gmail/", PRO_EMAIL)
        self.assertIn("accounts.google.com", ok.data["auth_url"])
        other = self.signed(
            "POST", f"/api/growth/programs/{self.other_org.slug}/connections/gmail/", PRO_EMAIL
        )
        self.assertEqual(other.status_code, 404)
