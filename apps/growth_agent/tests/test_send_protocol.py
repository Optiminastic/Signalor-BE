"""
The send protocol: claim, final check, provider call, result.

Every send leaves an append-only trail of ``ActionExecutionEvent`` rows:
``claimed`` -> ``provider_started`` (written just before the provider is
called) -> ``provider_result``. A worker that dies part-way leaves a trail
that says exactly what is known:

- claimed but never started: nothing went out, so the action goes back to
  approved and is sent normally later;
- started with no result: it may have gone out, so it becomes "outcome
  unknown" and is never sent again automatically.

Run: python manage.py test apps.growth_agent.tests.test_send_protocol --settings=config.settings.test
"""

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import ActionExecutionEvent, GrowthAction, GrowthProgram, GrowthTask, ImmutableRecordError
from ..services.control import dispatch, recovery
from .test_approval_flow import ApprovalFlowTestCase

Event = ActionExecutionEvent.Event
AFTER_CLAIM_TIMEOUT = recovery.CLAIM_TIMEOUT + timedelta(minutes=1)
AFTER_RESULT_DEADLINE = recovery.RESULT_DEADLINE + timedelta(minutes=1)


def at(delta: timedelta):
    """Run the recovery sweep as if ``delta`` had passed."""
    return recovery.recover_stuck(now=timezone.now() + delta)


class SendProtocolTestCase(ApprovalFlowTestCase):
    def setUp(self):
        super().setUp()
        self.approve()
        self.reload()

    def trail(self) -> list[str]:
        return list(ActionExecutionEvent.objects.filter(action=self.action).values_list("event", flat=True))

    def die_after_claim(self):
        """A worker that claimed the action and then stopped."""
        self.assertIsNotNone(dispatch._claim(self.action.id))
        self.reload()

    def die_after_provider_started(self):
        """A worker that passed the final check, started the call, then stopped."""
        claimed = dispatch._claim(self.action.id)
        self.assertTrue(dispatch._gate(claimed))
        self.reload()


class TrailTests(SendProtocolTestCase):
    def test_a_successful_send_leaves_the_full_trail(self):
        self.send()
        self.assertEqual(self.trail(), [Event.CLAIMED, Event.PROVIDER_STARTED, Event.PROVIDER_RESULT])
        result = ActionExecutionEvent.objects.get(action=self.action, event=Event.PROVIDER_RESULT)
        self.assertEqual(result.outcome, "succeeded")
        self.assertEqual(result.attempt, 1)
        self.assertEqual(result.version, self.version)

    def test_a_crash_during_the_call_records_an_unknown_result(self):
        self.channel.send_crash = True
        self.send()
        self.assertEqual(self.trail(), [Event.CLAIMED, Event.PROVIDER_STARTED, Event.PROVIDER_RESULT])
        result = ActionExecutionEvent.objects.get(action=self.action, event=Event.PROVIDER_RESULT)
        self.assertEqual(result.outcome, "unknown")
        self.assertEqual(self.action.state, GrowthAction.State.NEEDS_RECONCILIATION)

    def test_the_trail_cannot_be_changed_or_deleted(self):
        self.send()
        event = ActionExecutionEvent.objects.filter(action=self.action).first()
        event.outcome = "tampered"
        with self.assertRaises(ImmutableRecordError):
            event.save()
        with self.assertRaises(ImmutableRecordError):
            ActionExecutionEvent.objects.filter(action=self.action).delete()

    def test_an_attempt_cannot_start_the_provider_twice(self):
        self.die_after_provider_started()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ActionExecutionEvent.objects.create(
                organization=self.action.organization,
                action=self.action,
                version=self.version,
                attempt=1,
                event=Event.PROVIDER_STARTED,
            )


class FinalCheckTests(SendProtocolTestCase):
    def test_a_withdrawal_between_claim_and_call_stops_the_send(self):
        claimed = dispatch._claim(self.action.id)
        self.reload()
        self.assertEqual(self.decide("revoke").status_code, 200)
        self.assertFalse(dispatch._gate(claimed))
        self.assertEqual(self.channel.executed, [])
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.REVOKED)
        self.assertEqual(self.trail(), [Event.CLAIMED, Event.ABORTED])

    def test_a_kill_switch_between_claim_and_call_stops_the_send_and_keeps_the_approval(self):
        claimed = dispatch._claim(self.action.id)
        GrowthProgram.objects.filter(pk=self.program.pk).update(kill_switch=True)
        self.assertFalse(dispatch._gate(claimed))
        self.assertEqual(self.channel.executed, [])
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.APPROVED)
        self.assertEqual(self.task.status, GrowthTask.Status.AWAITING_APPROVAL)
        self.assertEqual(self.trail(), [Event.CLAIMED, Event.ABORTED])

    def test_after_an_aborted_attempt_the_next_send_is_attempt_two(self):
        claimed = dispatch._claim(self.action.id)
        GrowthProgram.objects.filter(pk=self.program.pk).update(kill_switch=True)
        dispatch._gate(claimed)
        GrowthProgram.objects.filter(pk=self.program.pk).update(kill_switch=False)
        self.send()
        self.assertEqual(len(self.channel.executed), 1)
        result = ActionExecutionEvent.objects.get(action=self.action, event=Event.PROVIDER_RESULT)
        self.assertEqual(result.attempt, 2)


class RecoveryTests(SendProtocolTestCase):
    def test_a_worker_that_died_before_the_call_is_put_back_and_sent_later(self):
        self.die_after_claim()
        self.assertEqual(self.action.state, GrowthAction.State.DISPATCHING)
        self.assertEqual(at(AFTER_CLAIM_TIMEOUT), 1)
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.APPROVED)
        self.assertEqual(self.trail(), [Event.CLAIMED, Event.ABORTED])
        self.send()
        self.assertEqual(len(self.channel.executed), 1)

    def test_a_worker_that_died_after_starting_the_call_leaves_the_outcome_unknown(self):
        self.die_after_provider_started()
        self.assertEqual(at(AFTER_RESULT_DEADLINE), 1)
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.NEEDS_RECONCILIATION)
        self.assertEqual(self.trail(), [Event.CLAIMED, Event.PROVIDER_STARTED, Event.PROVIDER_RESULT])
        self.assertEqual(
            ActionExecutionEvent.objects.get(action=self.action, event=Event.PROVIDER_RESULT).outcome,
            "unknown",
        )

    def test_an_unknown_outcome_is_never_sent_again(self):
        self.die_after_provider_started()
        at(AFTER_RESULT_DEADLINE)
        self.send()
        self.assertEqual(self.channel.executed, [])

    def test_a_send_still_in_progress_is_left_alone(self):
        self.die_after_claim()
        self.assertEqual(at(recovery.CLAIM_TIMEOUT - timedelta(minutes=1)), 0)
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.DISPATCHING)

    def test_a_started_call_is_not_given_up_on_before_the_deadline(self):
        self.die_after_provider_started()
        self.assertEqual(at(AFTER_CLAIM_TIMEOUT), 0)
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.DISPATCHING)


class InFlightRevokeTests(SendProtocolTestCase):
    def test_withdrawing_while_the_call_is_running_is_refused_honestly(self):
        self.die_after_provider_started()
        response = self.decide("revoke")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "in_flight")
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.DISPATCHING)

    def test_rejecting_while_the_call_is_running_is_refused(self):
        self.die_after_provider_started()
        self.assertEqual(self.decide("reject").status_code, 409)


class SchedulerTests(SendProtocolTestCase):
    def test_the_scheduler_recovers_a_stuck_send_before_sending(self):
        self.die_after_claim()
        later = timezone.now() + AFTER_CLAIM_TIMEOUT
        with mock.patch.object(recovery, "_now", return_value=later):
            call_command("run_growth_tasks", stdout=StringIO())
        self.reload()
        self.assertEqual(self.action.state, GrowthAction.State.SUCCEEDED)
        self.assertEqual(len(self.channel.executed), 1)
