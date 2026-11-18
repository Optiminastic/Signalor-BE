"""
Check that the Growth Agent scheduler and every active brand are running.

    python manage.py growth_health [--alert]

Exits 1 when there is a problem (usable as a container health check or a cron
monitor). ``--alert`` also raises each problem (error log + email to
GROWTH_OPS_ALERT_EMAILS, at most once an hour per problem).
"""

from django.core.management.base import BaseCommand, CommandError

from apps.growth_agent.services.scheduling import health


class Command(BaseCommand):
    help = "Report Growth Agent scheduler and brand heartbeat problems."

    def add_arguments(self, parser):
        parser.add_argument("--alert", action="store_true", help="Also log and email the problems.")

    def handle(self, *args, **options):
        found = health.problems()
        for problem in found:
            self.stdout.write(problem.message)
        if options["alert"]:
            health.alert(found)
        if found:
            raise CommandError(f"{len(found)} Growth Agent problem(s).")
        self.stdout.write(self.style.SUCCESS("Growth Agent is running."))
