"""
Unblock a Growth Agent action that stopped sending after repeated failures.

An action is blocked after ``ledger.MAX_CONSECUTIVE_FAILURES`` failed attempts
in a row. Fix the cause first (database, provider, connector), then:

    python manage.py growth_unblock_dispatch --action <public id> \
        --admin-username <superuser> --reason "What was wrong and what was done"

The command authenticates a named superuser with their password (typed, never
passed as an argument) and needs an interactive terminal. The unblock is
recorded in the attempt ledger and the audit log against that superuser.
"""

import getpass
import sys

from django.contrib.auth import authenticate
from django.core.management.base import BaseCommand, CommandError

from apps.growth_agent.models import AgentActionLog, GrowthAction
from apps.growth_agent.services.control import ledger
from apps.growth_agent.services.execution import audit

MIN_REASON_CHARS = 10


class Command(BaseCommand):
    help = "Unblock an action that stopped sending after repeated failed attempts."

    def add_arguments(self, parser):
        parser.add_argument("--action", required=True, help="The action's public id.")
        parser.add_argument("--admin-username", required=True, help="A superuser who authorizes this.")
        parser.add_argument("--reason", required=True, help="What was wrong and what was done about it.")

    def handle(self, *args, **options):
        if not sys.stdin.isatty():
            raise CommandError("This command needs an interactive terminal.")
        reason = options["reason"].strip()
        if len(reason) < MIN_REASON_CHARS:
            raise CommandError(f"Give a reason of at least {MIN_REASON_CHARS} characters.")
        password = getpass.getpass(f"Password for {options['admin_username']}: ")
        user = authenticate(username=options["admin_username"], password=password)
        if user is None or not user.is_active or not user.is_superuser:
            raise CommandError("Not authorized: an active superuser and their password are required.")
        action = (
            GrowthAction.objects.select_related("task__cycle__program")
            .filter(public_id=options["action"])
            .first()
        )
        if action is None:
            raise CommandError("No such action.")
        if not ledger.is_blocked(action.public_id):
            raise CommandError("This action is not blocked.")
        actor = f"admin:{user.pk}"
        try:
            ledger.unblock(action, actor=actor, reason=reason)
        except ledger.AlreadyUnblocked as exc:
            raise CommandError("Already unblocked; no attempt has run since.") from exc
        audit.record(
            action.task.cycle.program,
            action.task,
            "dispatch_unblocked",
            AgentActionLog.Outcome.SUCCEEDED,
            {"action": action.public_id, "admin": actor, "reason": reason[:200]},
        )
        self.stdout.write(
            self.style.SUCCESS(f"Unblocked {action.public_id}. It will be tried again next round.")
        )
