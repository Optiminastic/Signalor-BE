"""
Review a safety change (ownership, brand move, or connector identity) as a superuser.

    python manage.py growth_review_safety_change --change <id> \
        --outcome cleared|confirmed_incident|incident_resolved \
        --admin-username <superuser> --reason "..." [--recovery]

Needs an interactive terminal, the superuser's password and a fresh TOTP code.
``--recovery`` is required to clear a connector change the owner could have
confirmed themselves, and is recorded as such.
"""

import getpass
import sys

from django.core.management.base import BaseCommand, CommandError

from apps.growth_agent.models import AgentActionLog, GrowthSafetyChange, GrowthSafetyReview
from apps.growth_agent.services.control import safety, step_up
from apps.growth_agent.services.execution import audit

MIN_REASON_CHARS = 10


class Command(BaseCommand):
    help = "Review a Growth Agent safety change (superuser + TOTP)."

    def add_arguments(self, parser):
        parser.add_argument("--change", type=int, required=True)
        parser.add_argument("--outcome", required=True, choices=GrowthSafetyReview.Outcome.values)
        parser.add_argument("--admin-username", required=True)
        parser.add_argument("--reason", required=True)
        parser.add_argument("--recovery", action="store_true")

    def handle(self, *args, **options):
        if not sys.stdin.isatty():
            raise CommandError("This command needs an interactive terminal.")
        reason = options["reason"].strip()
        if len(reason) < MIN_REASON_CHARS:
            raise CommandError(f"Give a reason of at least {MIN_REASON_CHARS} characters.")
        change = GrowthSafetyChange.objects.filter(pk=options["change"]).first()
        if change is None:
            raise CommandError("No such change.")
        clearing_owner_change = options["outcome"] == "cleared" and safety.owner_may_confirm(change)
        if clearing_owner_change and not options["recovery"]:
            raise CommandError(
                "The brand owner confirms this kind of change. Use --recovery only if they can't."
            )
        try:
            user = step_up.verify_superuser(
                options["admin_username"],
                getpass.getpass(f"Password for {options['admin_username']}: "),
                getpass.getpass("Authenticator code: "),
            )
        except step_up.StepUpRefused as exc:
            raise CommandError(str(exc)) from exc
        kind = GrowthSafetyReview.StepUp.RECOVERY if options["recovery"] else GrowthSafetyReview.StepUp.TOTP
        try:
            review = safety.record_review(
                change, options["outcome"], step_up=kind, reason=reason, reviewer_user_id=user.pk
            )
        except safety.ReviewRefused as exc:
            raise CommandError(str(exc)) from exc
        program = getattr(change.organization, "growth_program", None)
        if program is not None:
            audit.record(
                program,
                None,
                "safety_reviewed",
                AgentActionLog.Outcome.SUCCEEDED,
                {
                    "change": change.pk,
                    "outcome": review.outcome,
                    "step_up": kind,
                    "admin": f"admin:{user.pk}",
                },
            )
        self.stdout.write(self.style.SUCCESS(f"Recorded {review.outcome} (review #{review.review_no})."))
