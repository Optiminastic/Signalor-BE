"""
State which database this process is about to touch, and refuse if it is not
the expected one. Run this before any migration:

    python manage.py check_db_target --expect-host <host> --expect-db <name> \
        --snapshot-ref <backup or snapshot id> [--role owner|runtime]

It only reads. It exits non-zero on any mismatch, on a missing expectation and
on a missing snapshot reference. It never prints a credential.
"""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.growth_agent.services import db_target

logger = logging.getLogger("apps")


class Command(BaseCommand):
    help = "Verify the database target (and optionally the role) before a migration."

    def add_arguments(self, parser):
        parser.add_argument("--expect-host", default=None, help="Host you believe you are connected to.")
        parser.add_argument(
            "--expect-db", default=None, help="Database name you believe you are connected to."
        )
        parser.add_argument(
            "--snapshot-ref", default=None, help="Id of the backup or snapshot taken beforehand."
        )
        parser.add_argument(
            "--role", choices=db_target.ROLE_CHOICES, default=None, help="Role this process must be."
        )

    def handle(self, *args, **options):
        target = db_target.describe(
            connection.settings_dict,
            vendor=connection.vendor,
            current_role=db_target.read_current_role(connection),
        )
        for line in db_target.render(target):
            self.stdout.write(line)

        problems = db_target.target_problems(
            target,
            expect_host=options["expect_host"],
            expect_db=options["expect_db"],
            snapshot_ref=options["snapshot_ref"],
        )
        if options["role"]:
            problems += db_target.role_problems(options["role"], db_target.read_role_facts(connection))
        if problems:
            raise CommandError("Database target check FAILED:\n- " + "\n- ".join(problems))

        snapshot_ref = options["snapshot_ref"].strip()
        logger.info(
            "check_db_target passed target=%s snapshot_ref=%s role=%s",
            db_target.fingerprint(target),
            snapshot_ref,
            options["role"] or "unchecked",
        )
        self.stdout.write(self.style.SUCCESS(f"OK: target verified, snapshot {snapshot_ref}."))
