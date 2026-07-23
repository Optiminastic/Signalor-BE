"""Per-email throttle for the onboarding write endpoint.

Per-IP limits (global middleware + DRF anon) catch script-kiddie attacks
from one box. A rotating-IP botnet can still hammer ``/organizations/onboard/``
by minting a fresh IP per request. Keying the throttle on the request-body
email forces an attacker to also rotate emails, which collapses the unique
org count they can create per email — exactly the abuse the bug report
describes.
"""

from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle


class OnboardEmailThrottle(SimpleRateThrottle):
    """Keyed on the normalized email in the POST body, falling back to per-IP so a
    missing field can't silently bypass it.

    The rate comes from the ``onboard_email`` scope in settings (base.py sets
    ``5/hour`` for prod/staging; development.py sets it to ``None`` to disable
    throttling in local dev). It is deliberately NOT hardcoded here — a class-level
    ``rate`` would override the settings scope and re-enable the throttle in dev.
    """

    scope = "onboard_email"

    def get_cache_key(self, request, view):
        from apps.accounts.subscription_utils import is_internal_email

        email = ""
        try:
            email = (request.data.get("email") or "").strip().lower()
        except Exception:
            email = ""
        # Internal (@optiminastic) accounts are our own dev/test users — never
        # throttle them, matching the "internal = unlimited" policy used across the
        # plan gates. Returning None makes SimpleRateThrottle allow the request.
        if email and is_internal_email(email):
            return None
        if not email:
            ident = self.get_ident(request)
            return self.cache_format % {"scope": self.scope, "ident": f"ip:{ident}"}
        return self.cache_format % {"scope": self.scope, "ident": f"email:{email}"}
