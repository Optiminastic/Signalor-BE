"""
Follow-up emails as their own approved actions.

When a follow-up is due on an open outreach thread, the scheduler drafts it as
a ``send_follow_up`` action linked to the thread and its step number. The owner
approves that exact email; its approval lasts ``FOLLOW_UP_APPROVAL_TTL``. Just
before sending, the final check confirms the thread is still open and nothing
else has been sent in it, and the channel reads the Gmail thread live: if the
contact replied, nothing is sent. A reply stops any pending follow-up. The
original task keeps its own status throughout.

Run: python manage.py test apps.growth_agent.tests.test_follow_up_actions --settings=config.settings.test
"""

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import ApprovalDecision, GrowthAction, GrowthTask, OutreachContact, OutreachThread
from ..services.control import actions, approvals, dispatch, expiry
from ..services.outreach import follow_up_actions as fua
from ..services.outreach import followups
from ..services.outreach.gmail import SentMessage, ThreadMessage
from .test_api import PRO_EMAIL
from .test_outreach import CH, OutreachTestCase

SENDER = "owner@brand-pro.com"
COMMAND = "apps.growth_agent.management.commands.run_growth_tasks"
CONTACT = "editor@site.com"


class FollowUpActionTestCase(OutreachTestCase):
    def setUp(self):
        super().setUp()
        # The first email went out: the task is done and the thread is open.
        GrowthTask.objects.filter(pk=self.task.pk).update(status=GrowthTask.Status.DONE)
        self.task.refresh_from_db()
        self.contact = OutreachContact.objects.create(
            organization=self.pro_org, email=CONTACT, domain="site.com", last_contacted_at=timezone.now()
        )
        self.thread = OutreachThread.objects.create(
            task=self.task,
            contact=self.contact,
            gmail_thread_id="t1",
            last_message_id="<first@mail>",
            subject="Your analytics roundup",
            next_follow_up_on=timezone.now().date(),
        )

    def prepare(self) -> int:
        return fua.prepare_due_follow_ups(timezone.now().date())

    def follow_up(self, sequence: int = 1) -> GrowthAction:
        return GrowthAction.objects.select_related("current_version", "task").get(
            thread=self.thread, sequence_no=sequence
        )

    def approve_action(self, action: GrowthAction) -> None:
        version = action.current_version
        request = approvals.DecisionRequest("approved", version.number, version.payload_hash, PRO_EMAIL)
        approvals.decide(self.program, action.public_id, request)

    def send_action(self, action: GrowthAction, replies=()):
        """Dispatch with Gmail faked. Returns the fake client."""
        messages = [ThreadMessage(SENDER, "pitch"), *replies]
        with mock.patch(f"{CH}.GmailClient") as client:
            client.return_value.thread_messages.return_value = messages
            client.return_value.send.return_value = SentMessage("m2", "t1", "<second@mail>")
            dispatch.dispatch(action.id)
        return client.return_value


class PreparationTests(FollowUpActionTestCase):
    def test_a_due_follow_up_is_drafted_as_its_own_action(self):
        self.assertEqual(self.prepare(), 1)
        action = self.follow_up()
        self.assertEqual(action.kind, GrowthAction.Kind.SEND_FOLLOW_UP)
        self.assertEqual(action.state, GrowthAction.State.AWAITING_APPROVAL)
        self.assertEqual(action.current_version.payload["subject"], "Re: Your analytics roundup")
        self.assertIn("just bumping this", action.current_version.payload["body"])
        self.assertIn("\n\n--\n", action.current_version.payload["body"])
        destination = action.current_version.destination
        self.assertEqual(destination["in_reply_to"], "<first@mail>")
        self.assertEqual(destination["gmail_thread_id"], "t1")
        self.assertEqual(destination["sequence"], 1)
        self.assertEqual(destination["sender"], SENDER)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, GrowthTask.Status.DONE)

    def test_a_step_is_drafted_only_once(self):
        self.prepare()
        self.assertEqual(self.prepare(), 0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            GrowthAction.objects.create(
                organization=self.pro_org,
                task=self.task,
                kind=GrowthAction.Kind.SEND_FOLLOW_UP,
                connector="gmail",
                thread=self.thread,
                sequence_no=1,
            )

    def test_nothing_is_drafted_when_not_due_replied_opted_out_paused_or_finished(self):
        cases = [
            {"next_follow_up_on": timezone.now().date() + timedelta(days=1)},
            {"status": OutreachThread.Status.REPLIED},
            {"followups_sent": len(followups.FOLLOW_UP_GAPS) + 1},
        ]
        for fields in cases:
            OutreachThread.objects.filter(pk=self.thread.pk).update(
                next_follow_up_on=timezone.now().date(), status=OutreachThread.Status.SENT, followups_sent=0
            )
            OutreachThread.objects.filter(pk=self.thread.pk).update(**fields)
            self.assertEqual(self.prepare(), 0, fields)
        OutreachThread.objects.filter(pk=self.thread.pk).update(
            status=OutreachThread.Status.SENT, followups_sent=0
        )
        OutreachContact.objects.filter(pk=self.contact.pk).update(opted_out=True)
        self.assertEqual(self.prepare(), 0)
        OutreachContact.objects.filter(pk=self.contact.pk).update(opted_out=False)
        self.program.status = "paused"
        self.program.save()
        self.assertEqual(self.prepare(), 0)

    def test_the_task_summary_keeps_showing_the_original_action(self):
        original = GrowthAction.objects.create(
            organization=self.pro_org,
            task=self.task,
            kind=GrowthAction.Kind.SEND_OUTREACH_EMAIL,
            connector="gmail",
            state=GrowthAction.State.SUCCEEDED,
        )
        self.prepare()
        self.assertEqual(actions.latest_action(self.task), original)


class SendTests(FollowUpActionTestCase):
    def setUp(self):
        super().setUp()
        self.prepare()
        self.action = self.follow_up()
        self.approve_action(self.action)

    def test_an_approved_follow_up_is_sent_into_the_same_thread(self):
        gmail = self.send_action(self.action)
        gmail.send.assert_called_once()
        args, kwargs = gmail.send.call_args
        self.assertEqual(args[0], CONTACT)
        self.assertEqual(kwargs["thread_id"], "t1")
        self.assertEqual(kwargs["in_reply_to"], "<first@mail>")
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.followups_sent, 1)
        self.assertEqual(self.thread.status, OutreachThread.Status.FOLLOWED_UP)
        self.assertEqual(self.thread.last_message_id, "<second@mail>")
        self.assertEqual(self.thread.next_follow_up_on, timezone.now().date() + followups.FOLLOW_UP_GAPS[1])
        self.assertEqual(self.follow_up().state, GrowthAction.State.SUCCEEDED)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, GrowthTask.Status.DONE)

    def test_a_reply_seen_live_just_before_sending_stops_the_send(self):
        gmail = self.send_action(self.action, replies=[ThreadMessage(CONTACT, "Sounds good")])
        gmail.send.assert_not_called()
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.status, OutreachThread.Status.REPLIED)
        self.assertEqual(self.follow_up().state, GrowthAction.State.FAILED)

    def test_a_reply_recorded_before_sending_stops_the_pending_follow_up(self):
        client = mock.Mock()
        client.thread_messages.return_value = [ThreadMessage(CONTACT, "Thanks, will look")]
        followups.process_thread(client, self.thread, SENDER)
        action = self.follow_up()
        self.assertEqual(action.state, GrowthAction.State.STOPPED)
        stop = ApprovalDecision.objects.filter(action=action).last()
        self.assertEqual(stop.decision, ApprovalDecision.Decision.STOPPED)
        self.assertEqual(stop.actor_role, "system")
        gmail = self.send_action(action)
        gmail.send.assert_not_called()

    def test_another_email_in_the_thread_since_drafting_blocks_the_send(self):
        OutreachThread.objects.filter(pk=self.thread.pk).update(last_message_id="<someone-else@mail>")
        gmail = self.send_action(self.action)
        gmail.send.assert_not_called()
        self.assertEqual(self.follow_up().state, GrowthAction.State.FAILED)

    def test_the_second_follow_up_comes_after_the_first_and_then_the_sequence_ends(self):
        self.send_action(self.action)
        OutreachThread.objects.filter(pk=self.thread.pk).update(next_follow_up_on=timezone.now().date())
        self.assertEqual(self.prepare(), 1)
        second = self.follow_up(2)
        self.assertIn("Last note from me", second.current_version.payload["body"])
        self.assertEqual(second.current_version.destination["in_reply_to"], "<second@mail>")
        self.approve_action(second)
        self.send_action(second)
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.followups_sent, 2)
        self.assertIsNone(self.thread.next_follow_up_on)
        self.assertEqual(self.prepare(), 0)


class SequenceStopTests(FollowUpActionTestCase):
    def setUp(self):
        super().setUp()
        self.prepare()
        self.action = self.follow_up()

    def test_rejecting_a_follow_up_ends_the_sequence_and_leaves_the_task_alone(self):
        version = self.action.current_version
        request = approvals.DecisionRequest("rejected", version.number, version.payload_hash, PRO_EMAIL)
        approvals.decide(self.program, self.action.public_id, request)
        self.assertEqual(self.prepare(), 0)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, GrowthTask.Status.DONE)

    def test_a_follow_up_approval_lasts_three_days(self):
        self.approve_action(self.action)
        action = self.follow_up()
        decided = ApprovalDecision.objects.get(action=action).created_at
        self.assertEqual(approvals.approval_expires_at(action), decided + approvals.FOLLOW_UP_APPROVAL_TTL)
        later = timezone.now() + approvals.FOLLOW_UP_APPROVAL_TTL + timedelta(minutes=1)
        with mock.patch.object(approvals, "_now", return_value=later):
            self.assertEqual(expiry.expire_due(), 1)
        self.assertEqual(self.follow_up().state, GrowthAction.State.EXPIRED)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, GrowthTask.Status.DONE)


class SchedulerTests(FollowUpActionTestCase):
    def test_the_scheduler_drafts_due_follow_ups(self):
        # The reply check reads Gmail; it has its own tests.
        with mock.patch(f"{COMMAND}.process_open_threads", return_value=0):
            call_command("run_growth_tasks", stdout=StringIO())
        self.assertEqual(GrowthAction.objects.filter(thread=self.thread).count(), 1)
