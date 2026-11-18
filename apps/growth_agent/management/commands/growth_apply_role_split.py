"""
Apply the Phase 1 database role split (see apps/growth_agent/db/role_split.py).

Run as the database administrator the first time (it takes over tables owned
by the old application role), and afterwards as signalor_owner after every
migration (scripts/migrate.sh does this):

    python manage.py growth_apply_role_split [--dry-run]

Does nothing and exits 0 when the roles don't exist yet (deployments that
haven't split), so the migrate wrapper is safe everywhere. Prints SQL, never
credentials. Everything runs in one transaction.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.growth_agent.db import role_split


class Command(BaseCommand):
    help = "Move tables to signalor_owner and narrow signalor_app's privileges."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print the SQL without running it.")
        parser.add_argument("--require-roles", action="store_true", help="Fail if the roles don't exist.")

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("The role split needs PostgreSQL.")
        with transaction.atomic(), connection.cursor() as cursor:
            if not role_split.roles_exist(cursor):
                if options["require_roles"]:
                    raise CommandError(
                        "signalor_owner / signalor_app don't exist: run deploy/sql/phase1_roles.sql."
                    )
                self.stdout.write("Role split skipped: the roles don't exist on this database.")
                return
            sql = role_split.statements(cursor) if options["dry_run"] else role_split.apply(cursor)
            remaining = [] if options["dry_run"] else role_split.problems(cursor)
        for statement in sql:
            self.stdout.write(f"{statement};")
        if remaining:
            raise CommandError("Split incomplete: " + " ".join(remaining))
        verb = "Would run" if options["dry_run"] else "Applied"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(sql)} statements."))
