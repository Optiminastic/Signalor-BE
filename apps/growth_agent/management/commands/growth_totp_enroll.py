"""
Enrol an authenticator (TOTP) for a superuser, needed for safety reviews and
other Growth Agent recovery commands.

    python manage.py growth_totp_enroll --username <superuser>

Needs an interactive terminal and the superuser's password. Prints the secret
once (as an otpauth URL for the authenticator app); it is never logged. The
device only counts once confirmed with a first valid code.
"""

import getpass
import sys

from django.contrib.auth import authenticate
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_totp.models import TOTPDevice

DEVICE_NAME = "growth-agent"


class Command(BaseCommand):
    help = "Enrol a TOTP authenticator for a superuser."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)

    def handle(self, *args, **options):
        if not sys.stdin.isatty():
            raise CommandError("This command needs an interactive terminal.")
        user = authenticate(username=options["username"], password=getpass.getpass("Password: "))
        if user is None or not user.is_active or not user.is_superuser:
            raise CommandError("Not authorized: an active superuser and their password are required.")
        TOTPDevice.objects.filter(user=user, name=DEVICE_NAME, confirmed=False).delete()
        device = TOTPDevice.objects.create(user=user, name=DEVICE_NAME, confirmed=False)
        self.stdout.write("Add this to your authenticator app (shown once):")
        self.stdout.write(device.config_url)
        code = input("Enter the 6-digit code it shows: ").strip()
        if not code.isdigit() or not device.verify_token(code):
            device.delete()
            raise CommandError("That code didn't match. Nothing was enrolled; run the command again.")
        device.confirmed = True
        device.save(update_fields=["confirmed"])
        self.stdout.write(self.style.SUCCESS("Authenticator enrolled."))
