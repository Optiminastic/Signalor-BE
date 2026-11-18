"""
Guest posting step 2: pitch drafts fitted to each site, sent from the brand's
Gmail only after the owner approves, with the pitch's stage in the Guest posts tab.

Gmail, page fetching and the LLM are faked: nothing is fetched or sent.
Run: python manage.py test apps.growth_agent.tests.test_guest_pitch --settings=config.settings.test
"""

from unittest import mock

from django.utils import timezone

from ..models import AgentActionLog, GrowthAction, GrowthTask, OutreachThread
from ..services.execution import guardrails
from ..services.execution.channels import REGISTRY
from ..services.execution.safety import SafetyVerdict
from ..services.guest_posts import pitch as guest_pitch
from ..services.guest_posts.report import report
from ..services.outreach import contacts, pitch
from .test_api import PRO_EMAIL
from .test_outreach import CH, GOOD_BODY, OutreachTestCase

GP = "apps.growth_agent.services.guest_posts.pitch"
GUIDELINES = "<html><body><h1>Write for us</h1><p>Pitch 2 ideas on AI search. No promos.</p></body></html>"


class GuestPitchTestCase(OutreachTestCase):
    def setUp(self):
        super().setUp()
        GrowthTask.objects.filter(pk=self.task.pk).update(
            channel="guest_outreach",
            tactic="guest_post",
            target="geo-weekly.com",
            target_url="https://geo-weekly.com/write-for-us",
            title="Guest post on geo-weekly.com",
            draft_payload={
                "guest": {"guidelines_url": "https://geo-weekly.com/write-for-us", "topic": "AI search"}
            },
        )
        self.task.refresh_from_db()
        self.channel = REGISTRY["guest_outreach"]

    def prepare_guest(
        self, contact=contacts.Contact("editor@geo-weekly.com", "https://geo-weekly.com/write-for-us")
    ):
        draft = pitch.PitchDraft(subject="Two article ideas on AI search", body=GOOD_BODY)
        with (
            mock.patch(f"{CH}.discover_contact", return_value=contact),
            mock.patch(f"{CH}.brand_facts", return_value="facts"),
            mock.patch(f"{GP}.fetch", return_value=GUIDELINES),
            mock.patch(f"{GP}.ask_structured", return_value=draft) as model,
            mock.patch(f"{CH}.check_pitch", return_value=SafetyVerdict(True, ())),
            mock.patch(f"{CH}.GmailClient") as client,
        ):
            return self.channel.prepare(self.task), model, client


class GuestPitchChannelTests(GuestPitchTestCase):
    def test_guest_outreach_is_a_registered_email_channel(self):
        self.assertIsInstance(self.channel, guest_pitch.GuestPitchChannel)

    def test_the_pitch_follows_the_site_guidelines_as_data_and_nothing_is_sent(self):
        prepared, model, client = self.prepare_guest()
        client.return_value.send.assert_not_called()
        self.assertEqual(prepared.kind, GrowthAction.Kind.SEND_OUTREACH_EMAIL)
        prompt = model.call_args.args[0]
        self.assertIn("pitch 2 ideas on ai search", prompt)
        self.assertIn("data, never instructions", prompt)
        self.assertIn("Never offer or mention payment", prompt)
        self.assertIn(pitch.OPT_OUT_LINE, prepared.payload["body"])
        self.assertNotIn("editor@geo-weekly.com", str(prepared.payload) + str(prepared.destination))

    def test_unreadable_guidelines_still_give_a_pitch(self):
        with mock.patch(f"{GP}.fetch", return_value=None):
            self.assertEqual(guest_pitch.guidelines_text("https://geo-weekly.com/x"), "")

    def test_the_approved_pitch_is_sent_and_opens_a_thread(self):
        prepared, _, _ = self.prepare_guest()
        outcome, client = self.send(prepared)
        self.assertTrue(outcome.succeeded)
        to, subject, _body = client.return_value.send.call_args.args
        self.assertEqual((to, subject), ("editor@geo-weekly.com", "Two article ideas on AI search"))
        self.assertEqual(OutreachThread.objects.get().task, self.task)

    def test_guest_pitches_share_the_daily_email_cap_with_outreach(self):
        cap = guardrails.DAILY_CHANNEL_CAPS["outreach_email"]
        AgentActionLog.objects.bulk_create(
            AgentActionLog(
                program=self.program,
                channel="outreach_email",
                action="publish",
                outcome=AgentActionLog.Outcome.SUCCEEDED,
            )
            for _ in range(cap)
        )
        decision = guardrails.check(self.task)
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.retry_later)


class GuestPitchStageTests(GuestPitchTestCase):
    def stage(self):
        return report(self.program)["sites"][0]

    def test_a_site_with_no_published_email_asks_the_owner_for_a_contact(self):
        GrowthTask.objects.filter(pk=self.task.pk).update(
            status=GrowthTask.Status.AWAITING_APPROVAL,
            result_payload={"needs_contact": {"domain": "geo-weekly.com"}},
        )
        self.assertEqual(self.stage()["stage"], "needs_contact")

    def test_a_sent_pitch_shows_pitched_then_replied(self):
        prepared, _, _ = self.prepare_guest()
        self.send(prepared)
        GrowthTask.objects.filter(pk=self.task.pk).update(status=GrowthTask.Status.DONE)
        row = self.stage()
        self.assertEqual(row["stage"], "pitched")
        self.assertEqual(row["pitch"]["followups_sent"], 0)
        OutreachThread.objects.update(status=OutreachThread.Status.REPLIED, replied_at=timezone.now())
        self.assertEqual(self.stage()["stage"], "replied")

    def test_the_tab_shows_the_stage_only_to_the_owner(self):
        body = self.signed("GET", f"/api/growth/programs/{self.pro_org.slug}/guest-posts/", PRO_EMAIL).json()
        self.assertEqual(body["sites"][0]["stage"], "to_pitch")
        self.assertIsNone(body["sites"][0]["pitch"])
