"""
Concurrent requests on the same action, on real Postgres.

SQLite ignores ``SELECT ... FOR UPDATE``, so these only run on Postgres
(``config.settings.test_postgres``); on SQLite they are skipped. Each test
releases two operations at the same moment, from two threads with their own
database connections, and repeats that several times. What they check is the
invariant, whichever thread wins: never two sends, never two decisions, never
an approval left standing on a superseded version.

Run: TEST_DATABASE_URL=... python manage.py test apps.growth_agent.tests.test_concurrency_pg \
        --settings=config.settings.test_postgres
"""

import threading
from datetime import timedelta
from functools import partial
from unittest import mock, skipUnless

from django.db import connection, connections
from django.test import TransactionTestCase
from django.utils import timezone

from apps.organizations.models import Organization

from ..models import ApprovalDecision, GrowthAction, GrowthCycle, GrowthProgram, GrowthTask
from ..services.control import approvals, dispatch, editing, resolution
from ..services.execution import executor, guardrails
from .test_execution_flow import REGISTRY, FakeChannel

OWNER = "owner@concurrency.example"
ROUNDS = 8


def run_together(*operations):
    """Start every operation at the same moment, each in its own thread and
    connection. Returns each one's result or the exception it raised."""
    barrier = threading.Barrier(len(operations))
    outcomes = [None] * len(operations)

    def worker(index, operation):
        try:
            barrier.wait()
            outcomes[index] = operation()
        except Exception as exc:  # noqa: BLE001 - recorded for the assertions
            outcomes[index] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i, op)) for i, op in enumerate(operations)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return outcomes


@skipUnless(connection.vendor == "postgresql", "needs Postgres row locks")
class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        org = Organization.objects.create(name="Concurrency", url="https://c.example", owner_email=OWNER)
        self.program = GrowthProgram.objects.create(
            organization=org, created_by_email=OWNER, status=GrowthProgram.Status.ACTIVE
        )
        today = timezone.now().date()
        self.cycle = GrowthCycle.objects.create(
            program=self.program, starts_on=today, ends_on=today + timedelta(days=27)
        )
        self.channel = FakeChannel()
        patchers = [
            mock.patch.dict(REGISTRY, {"owned_content": self.channel}),
            # Each test sends many actions on one day; the real daily cap and
            # monthly budget would stop drafting after the first few.
            mock.patch.dict(guardrails.DAILY_CHANNEL_CAPS, {"owned_content": 1000}),
            mock.patch.object(guardrails, "MAX_ACTIONS_PER_CYCLE", 1000),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def draft(self, number: int) -> GrowthAction:
        task = GrowthTask.objects.create(
            cycle=self.cycle,
            week=1,
            scheduled_for=timezone.now().date(),
            channel="owned_content",
            tactic="answer_content",
            title=f"Page {number}",
            target="AI citations",
        )
        executor.run_task(task.id)
        return self.fresh(GrowthAction.objects.get(task=task))

    def fresh(self, action: GrowthAction) -> GrowthAction:
        return GrowthAction.objects.select_related("current_version").get(pk=action.pk)

    def decision(self, action: GrowthAction, decision: str) -> approvals.DecisionRequest:
        version = action.current_version
        return approvals.DecisionRequest(
            decision=decision,
            version_number=version.number,
            payload_hash=version.payload_hash,
            actor_email=OWNER,
        )

    def approve(self, action: GrowthAction) -> GrowthAction:
        approvals.decide(self.program, action.public_id, self.decision(action, "approved"))
        return self.fresh(action)

    def test_two_identical_approvals_record_one_decision(self):
        for number in range(ROUNDS):
            action = self.draft(number)
            request = self.decision(action, "approved")
            decide = partial(approvals.decide, self.program, action.public_id, request)
            outcomes = run_together(decide, decide)
            self.assertFalse([o for o in outcomes if isinstance(o, Exception)], outcomes)
            self.assertEqual(ApprovalDecision.objects.filter(action=action).count(), 1)

    def edit_request(self, action: GrowthAction, title: str) -> editing.EditRequest:
        version = action.current_version
        article = version.payload["article"]
        return editing.EditRequest(
            version_number=version.number,
            payload_hash=version.payload_hash,
            fields={
                "title": title,
                "meta_description": article["meta_description"],
                "slug": version.payload["slug"],
                "answer": article["answer"],
            },
            actor_email=OWNER,
        )

    def test_an_approval_racing_an_edit_never_stands_on_an_old_version(self):
        for number in range(ROUNDS):
            action = self.draft(number)
            run_together(
                partial(approvals.decide, self.program, action.public_id, self.decision(action, "approved")),
                partial(
                    editing.edit,
                    self.program,
                    action.public_id,
                    self.edit_request(action, f"Edited {number}"),
                ),
            )
            action = self.fresh(action)
            # Whichever ran first, the edit lands, and nothing approves version 2.
            self.assertEqual(action.current_version.number, 2)
            self.assertEqual(action.state, GrowthAction.State.AWAITING_APPROVAL)
            self.assertFalse(approvals.has_valid_approval(action))

    def test_two_sends_of_one_approved_action_send_once(self):
        for number in range(ROUNDS):
            action = self.approve(self.draft(number))
            before = len(self.channel.executed)
            send = partial(dispatch.dispatch, action.id)
            run_together(send, send)
            self.assertEqual(len(self.channel.executed) - before, 1)
            self.assertEqual(self.fresh(action).state, GrowthAction.State.SUCCEEDED)

    def test_a_rejection_racing_a_send_never_leaves_a_sent_action_rejected(self):
        for number in range(ROUNDS):
            action = self.approve(self.draft(number))
            before = len(self.channel.executed)
            run_together(
                partial(approvals.decide, self.program, action.public_id, self.decision(action, "rejected")),
                partial(dispatch.dispatch, action.id),
            )
            sent = len(self.channel.executed) - before
            state = self.fresh(action).state
            if sent:
                self.assertEqual(state, GrowthAction.State.SUCCEEDED)
            else:
                self.assertEqual(state, GrowthAction.State.REJECTED)
            self.assertLessEqual(sent, 1)

    def resolution_request(self, action: GrowthAction, name: str) -> resolution.ResolutionRequest:
        version = action.current_version
        return resolution.ResolutionRequest(
            resolution=name,
            version_number=version.number,
            payload_hash=version.payload_hash,
            actor_email=OWNER,
        )

    def test_two_different_resolutions_record_exactly_one(self):
        self.channel.send_crash = True
        for number in range(ROUNDS):
            action = self.approve(self.draft(number))
            dispatch.dispatch(action.id)
            action = self.fresh(action)
            self.assertEqual(action.state, GrowthAction.State.NEEDS_RECONCILIATION)
            outcomes = run_together(
                partial(
                    resolution.resolve,
                    self.program,
                    action.public_id,
                    self.resolution_request(action, "confirmed_sent"),
                ),
                partial(
                    resolution.resolve,
                    self.program,
                    action.public_id,
                    self.resolution_request(action, "cancel"),
                ),
            )
            refused = [o for o in outcomes if isinstance(o, approvals.DecisionRefused)]
            self.assertEqual(len(refused), 1, outcomes)
            self.assertEqual(refused[0].code, "illegal_state")
            resolved = ApprovalDecision.objects.filter(
                action=action, decision__in=["resolved_sent", "resolved_cancelled"]
            )
            self.assertEqual(resolved.count(), 1)
