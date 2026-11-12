"""
The attempt ledger: every send attempt is paid for before it runs.

Before an attempt, a numbered ``attempt`` row is written to the append-only
``DispatchAttemptLedger``. If it can't be written, the attempt doesn't happen.
Afterwards a ``result`` row records how it ended. Consecutive failures are
counted from the ledger: each one adds a growing wait, and after
``MAX_CONSECUTIVE_FAILURES`` the action is blocked until an operator unblocks
it. Ticket numbers only go up and are never reused.

Run: python manage.py test apps.growth_agent.tests.test_attempt_ledger --settings=config.settings.test
"""

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import (
    ActionExecutionEvent,
    DispatchAttemptLedger,
    GrowthAction,
    GrowthProgram,
    ImmutableRecordError,
)
from ..services.control import dispatch, ledger
from .test_approval_flow import ApprovalFlowTestCase

Kind = DispatchAttemptLedger.Kind


def later(minutes: float):
    return mock.patch.object(ledger, "_now", return_value=timezone.now() + timedelta(minutes=minutes))


class LedgerTestCase(ApprovalFlowTestCase):
    def setUp(self):
        super().setUp()
        self.approve()
        self.reload()

    def rows(self) -> list[tuple[str, int, str]]:
        return list(
            DispatchAttemptLedger.objects.filter(action_public_id=self.action.public_id)
            .order_by("id")
            .values_list("kind", "seq", "outcome")
        )

    def failed_tickets(self, count: int, start: int = 1):
        """Write ``count`` consecutive failed attempts, as a stuck action would."""
        for seq in range(start, start + count):
            ledger.write(self.action, Kind.ATTEMPT, seq)
            ledger.write(self.action, Kind.RESULT, seq, outcome=ledger.LOCK_CONFLICT)


class TicketTests(LedgerTestCase):
    def test_a_send_pays_for_its_attempt_first_and_records_the_result(self):
        self.send()
        self.assertEqual(self.rows(), [(Kind.ATTEMPT, 1, ""), (Kind.RESULT, 1, ledger.PROVIDER_STARTED)])
        self.assertEqual(len(self.channel.executed), 1)

    def test_ticket_numbers_only_go_up(self):
        GrowthProgram.objects.filter(pk=self.program.pk).update(kill_switch=True)
        self.send()  # stopped by the guardrail: a wait, not a failure
        GrowthProgram.objects.filter(pk=self.program.pk).update(kill_switch=False)
        self.send()
        self.assertEqual(
            self.rows(),
            [
                (Kind.ATTEMPT, 1, ""),
                (Kind.RESULT, 1, ledger.PREFLIGHT_WAIT),
                (Kind.ATTEMPT, 2, ""),
                (Kind.RESULT, 2, ledger.PROVIDER_STARTED),
            ],
        )

    def test_a_ticket_number_already_taken_means_no_attempt(self):
        # Another worker reserved the same number first.
        ledger.write(self.action, Kind.ATTEMPT, 1)
        with (
            mock.patch.object(ledger, "_eligible", return_value=True),
            mock.patch.object(ledger, "_next_seq", return_value=1),
        ):
            self.assertIsNone(ledger.reserve(self.action))
            self.send()
        self.assertEqual(self.channel.executed, [])
        self.assertEqual(self.action.state, GrowthAction.State.APPROVED)

    def test_if_the_ledger_cannot_be_written_nothing_is_sent(self):
        with mock.patch.object(ledger, "write", side_effect=RuntimeError("database unavailable")):
            self.send()
        self.assertEqual(self.channel.executed, [])
        self.assertEqual(self.action.state, GrowthAction.State.APPROVED)

    def test_a_number_is_used_once_per_kind(self):
        ledger.write(self.action, Kind.ATTEMPT, 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ledger.write(self.action, Kind.ATTEMPT, 1)

    def test_the_ledger_cannot_be_changed_or_deleted(self):
        self.send()
        row = DispatchAttemptLedger.objects.first()
        row.outcome = "tampered"
        with self.assertRaises(ImmutableRecordError):
            row.save()
        with self.assertRaises(ImmutableRecordError):
            DispatchAttemptLedger.objects.all().delete()


class FailureCountTests(LedgerTestCase):
    def test_failures_in_a_row_are_counted(self):
        self.failed_tickets(3)
        self.assertEqual(ledger.consecutive_failures(self.action.public_id), 3)

    def test_a_started_call_or_a_guardrail_wait_resets_the_count(self):
        self.failed_tickets(2)
        ledger.write(self.action, Kind.ATTEMPT, 3)
        ledger.write(self.action, Kind.RESULT, 3, outcome=ledger.PREFLIGHT_WAIT)
        self.failed_tickets(1, start=4)
        self.assertEqual(ledger.consecutive_failures(self.action.public_id), 1)

    def test_a_ticket_without_a_result_counts_as_a_failure(self):
        ledger.write(self.action, Kind.ATTEMPT, 1)
        self.assertEqual(ledger.consecutive_failures(self.action.public_id), 1)

    def test_after_a_failure_the_next_attempt_waits(self):
        self.failed_tickets(1)
        self.send()
        self.assertEqual(self.channel.executed, [])
        with later(2):
            self.send()
        self.assertEqual(len(self.channel.executed), 1)

    def test_the_wait_grows_with_each_failure_and_is_capped(self):
        self.assertEqual(ledger.backoff(1), timedelta(minutes=1))
        self.assertEqual(ledger.backoff(3), timedelta(minutes=4))
        self.assertEqual(ledger.backoff(9), timedelta(minutes=ledger.MAX_BACKOFF_MINUTES))


class BlockingTests(LedgerTestCase):
    def test_after_too_many_failures_the_action_is_blocked(self):
        self.failed_tickets(ledger.MAX_CONSECUTIVE_FAILURES)
        with later(24 * 60):
            self.send()
        self.assertEqual(self.channel.executed, [])
        self.assertEqual(len(self.rows()), 2 * ledger.MAX_CONSECUTIVE_FAILURES)  # no new ticket
        self.assertTrue(ledger.is_blocked(self.action.public_id))

    def test_unblocking_resets_the_count_and_the_next_ticket_is_new(self):
        self.failed_tickets(ledger.MAX_CONSECUTIVE_FAILURES)
        ledger.unblock(self.action, actor="admin:1", reason="Database was down, now fixed.")
        self.assertEqual(ledger.consecutive_failures(self.action.public_id), 0)
        self.send()
        self.assertEqual(len(self.channel.executed), 1)
        attempts = [seq for kind, seq, _ in self.rows() if kind == Kind.ATTEMPT]
        self.assertEqual(attempts[-1], ledger.MAX_CONSECUTIVE_FAILURES + 1)

    def test_unblocking_twice_without_an_attempt_is_reported(self):
        self.failed_tickets(ledger.MAX_CONSECUTIVE_FAILURES)
        ledger.unblock(self.action, actor="admin:1", reason="Fixed the cause.")
        with self.assertRaises(ledger.AlreadyUnblocked):
            ledger.unblock(self.action, actor="admin:1", reason="Again.")


class UnblockCommandTests(LedgerTestCase):
    def setUp(self):
        super().setUp()
        self.failed_tickets(ledger.MAX_CONSECUTIVE_FAILURES)
        self.admin = get_user_model().objects.create_superuser(
            "ops", "ops@signalor.example", password="correct horse"
        )

    def run_command(self, *, tty=True, password="correct horse", **options):
        args = {"action": self.action.public_id, "admin_username": "ops", **options}
        with (
            mock.patch("sys.stdin.isatty", return_value=tty),
            mock.patch("getpass.getpass", return_value=password),
        ):
            call_command("growth_unblock_dispatch", stdout=StringIO(), **args)

    def test_a_superuser_with_a_reason_can_unblock(self):
        self.run_command(reason="Provider outage is over.")
        self.assertFalse(ledger.is_blocked(self.action.public_id))
        row = DispatchAttemptLedger.objects.filter(kind=Kind.UNBLOCK).get()
        self.assertEqual(row.actor, f"admin:{self.admin.pk}")

    def test_it_refuses_without_a_terminal_a_reason_or_the_right_password(self):
        refused = (
            {"tty": False, "reason": "A good enough reason."},
            {"reason": ""},
            {"password": "wrong", "reason": "A good enough reason."},
        )
        for options in refused:
            with self.assertRaises(CommandError):
                self.run_command(**options)
        self.assertTrue(ledger.is_blocked(self.action.public_id))


class BrandDeletionTests(LedgerTestCase):
    def test_deleting_the_brand_removes_its_trail_and_ledger(self):
        self.send()
        self.assertTrue(DispatchAttemptLedger.objects.exists())
        self.assertTrue(ActionExecutionEvent.objects.exists())
        self.pro_org.delete()
        self.assertFalse(DispatchAttemptLedger.objects.exists())
        self.assertFalse(ActionExecutionEvent.objects.exists())
