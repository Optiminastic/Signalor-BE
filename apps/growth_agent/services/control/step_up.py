"""
Superuser step-up for recovery and safety reviews: an active superuser, their
password, and a fresh TOTP code from their confirmed django-otp device.

A code is accepted at most once (django-otp records the last used time step,
so a replay fails) and repeated failures are throttled by django-otp. Never
accepts an emailed owner code, and the owner flow never accepts TOTP.
"""

from __future__ import annotations

from django.contrib.auth import authenticate
from django_otp.plugins.otp_totp.models import TOTPDevice


class StepUpRefused(Exception):
    """Not authorized. The message is safe to print."""


def verify_superuser(username: str, password: str, code: str):
    """Return the superuser if the password and a fresh TOTP code are right; else raise."""
    user = authenticate(username=username, password=password)
    if user is None or not user.is_active or not user.is_superuser:
        raise StepUpRefused("Not authorized: an active superuser and their password are required.")
    devices = list(TOTPDevice.objects.filter(user=user, confirmed=True))
    if not devices:
        raise StepUpRefused("This superuser has no confirmed authenticator. Run growth_totp_enroll first.")
    code = (code or "").strip()
    if not code.isdigit():
        raise StepUpRefused("The authenticator code was not accepted.")
    for device in devices:
        allowed, _ = device.verify_is_allowed()
        if allowed and device.verify_token(code):
            return user
    raise StepUpRefused("The authenticator code was not accepted.")
