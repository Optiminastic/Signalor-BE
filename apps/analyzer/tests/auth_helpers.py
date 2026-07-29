"""Shared helpers for exercising verified-identity scoping in view tests.

Authorization is derived from ``request.user``, which only
``BetterAuthJWTAuthentication`` sets and only after verifying a signed JWT.
Tests therefore cannot "log in" by sending a header - they patch in the same
principal the authenticator would have produced.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from django.test import override_settings

# Any non-empty value works: the authenticator is never actually invoked in
# tests, but views distinguish "no verified caller" from "this deployment cannot
# authenticate anyone" by whether a JWKS URL is configured.
TEST_JWKS_URL = "https://auth.example/jwks"


def as_verified(email: str):
    """Patch in a verified principal for ``email``. Use as a context manager."""
    from apps.accounts.authentication import VerifiedUser

    return patch(
        "rest_framework.request.Request.user",
        new_callable=lambda: property(lambda _self: VerifiedUser(email=email)),
    )


@contextmanager
def signed_in(email: str, **settings_overrides):
    """A configured deployment with ``email`` authenticated.

    The common case for endpoints that require a verified caller: without the
    JWKS setting they return 503 (auth unavailable) rather than exercising the
    scoping logic under test.
    """
    with override_settings(BETTER_AUTH_JWKS_URL=TEST_JWKS_URL, **settings_overrides):
        with as_verified(email):
            yield
