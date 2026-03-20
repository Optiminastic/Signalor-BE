"""
Reset the database: delete all data and keep everything fresh.

Usage:
  python manage.py reset_db              # prompts for confirmation
  python manage.py reset_db --no-input   # skip confirmation (for scripts)

Removes all rows from all tables but keeps the schema. Uses TRUNCATE CASCADE
for PostgreSQL to handle foreign keys correctly.
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Delete all data from the database and reset to a fresh state."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Skip confirmation prompt.",
        )

    def handle(self, *args, **options):
        if not options["no_input"]:
            self.stdout.write(self.style.WARNING("This will DELETE ALL DATA in the database."))
            confirm = input("Type 'yes' to confirm: ")
            if confirm.lower() != "yes":
                self.stdout.write(self.style.ERROR("Aborted."))
                return

        engine = connection.vendor
        if engine == "postgresql":
            self._flush_postgresql()
        else:
            self._flush_default()

        self.stdout.write(self.style.SUCCESS("Database reset complete. Everything is fresh."))

    def _flush_postgresql(self):
        """Use TRUNCATE ... CASCADE for PostgreSQL (fixes FK and 'user' table issues)."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT quote_ident(table_schema) || '.' || quote_ident(table_name)
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
            tables = [row[0] for row in cursor.fetchall()]
        if not tables:
            return
        tables_sql = ", ".join(tables)
        with connection.cursor() as cursor:
            cursor.execute(f"TRUNCATE {tables_sql} RESTART IDENTITY CASCADE")

    def _flush_default(self):
        """Fallback for SQLite and other backends."""
        from django.core.management import call_command
        call_command("flush", "--no-input")
