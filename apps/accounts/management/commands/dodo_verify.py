"""
Verify Dodo Payments API key against live and test endpoints (read-only list call).

Usage:
  python manage.py dodo_verify

Shows which environment(s) accept your key so you can align DODO_LIVE_MODE.
"""

from django.core.management.base import BaseCommand

from apps.accounts.dodo_env import normalized_dodo_api_key


class Command(BaseCommand):
    help = "Test Dodo API key against live and test environments (products.list)."

    def handle(self, *args, **options):
        key = normalized_dodo_api_key()
        if not key:
            self.stderr.write(
                self.style.ERROR(
                    "No API key: set DODO_API_KEY or DODO_PAYMENTS_API_KEY in ranking-be/.env"
                )
            )
            return

        from dodopayments import AuthenticationError, DodoPayments

        for label, env in (("live", "live_mode"), ("test", "test_mode")):
            try:
                client = DodoPayments(bearer_token=key, environment=env)
                page = client.products.list(page_size=1)
                next(iter(page), None)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{label}: OK — key is accepted (use DODO_LIVE_MODE="
                        f"{'true' if env == 'live_mode' else 'false'})"
                    )
                )
            except AuthenticationError as exc:
                self.stdout.write(
                    self.style.ERROR(f"{label}: 401 unauthorized — {exc}")
                )
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"{label}: error — {exc}"))
