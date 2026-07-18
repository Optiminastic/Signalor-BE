"""Unit tests for better-auth JWT verification and identity resolution.

Pure-logic: the JWKS network fetch and signature verification are mocked, so no
network and no DB (SimpleTestCase).
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.accounts import authentication as auth
from apps.accounts.authentication import BetterAuthJWTAuthentication, VerifiedUser
from apps.accounts.identity import resolve_request_email, verified_email


def _request(auth_header: str | None = None):
    meta = {}
    if auth_header is not None:
        meta["HTTP_AUTHORIZATION"] = auth_header
    return SimpleNamespace(META=meta)


class AuthClassTests(SimpleTestCase):
    def setUp(self):
        auth._jwks_clients.clear()  # don't leak a cached client between tests

    def test_dormant_when_no_jwks_url(self):
        with override_settings(BETTER_AUTH_JWKS_URL=""):
            self.assertIsNone(BetterAuthJWTAuthentication().authenticate(_request("bearer a.b.c")))

    @override_settings(BETTER_AUTH_JWKS_URL="https://app.example.com/api/auth/jwks")
    def test_no_bearer_header_returns_none(self):
        self.assertIsNone(BetterAuthJWTAuthentication().authenticate(_request(None)))

    @override_settings(BETTER_AUTH_JWKS_URL="https://app.example.com/api/auth/jwks")
    def test_non_jwt_bearer_falls_through(self):
        # An sk_live_ API key is a bearer token but not a JWT — must return None, not 401,
        # so the public_api Bearer auth can handle it.
        self.assertIsNone(BetterAuthJWTAuthentication().authenticate(_request("Bearer sk_live_abc123")))

    @override_settings(BETTER_AUTH_JWKS_URL="https://app.example.com/api/auth/jwks")
    def test_valid_token_returns_verified_user(self):
        fake_key = SimpleNamespace(key="pub")
        with patch.object(auth, "_client") as mclient, patch.object(
            auth.jwt, "decode", return_value={"email": "User@Co.com", "sub": "u_1"}
        ):
            mclient.return_value.get_signing_key_from_jwt.return_value = fake_key
            result = BetterAuthJWTAuthentication().authenticate(_request("Bearer a.b.c"))
        self.assertIsNotNone(result)
        user, token = result
        self.assertIsInstance(user, VerifiedUser)
        self.assertEqual(user.email, "user@co.com")  # normalized
        self.assertTrue(user.is_authenticated)
        self.assertEqual(token, "a.b.c")

    @override_settings(BETTER_AUTH_JWKS_URL="https://app.example.com/api/auth/jwks")
    def test_invalid_signature_returns_none(self):
        with patch.object(auth, "_client") as mclient, patch.object(
            auth.jwt, "decode", side_effect=Exception("bad signature")
        ):
            mclient.return_value.get_signing_key_from_jwt.return_value = SimpleNamespace(key="pub")
            self.assertIsNone(BetterAuthJWTAuthentication().authenticate(_request("Bearer a.b.c")))

    @override_settings(BETTER_AUTH_JWKS_URL="https://app.example.com/api/auth/jwks")
    def test_token_without_email_claim_returns_none(self):
        with patch.object(auth, "_client") as mclient, patch.object(
            auth.jwt, "decode", return_value={"sub": "u_1"}
        ):
            mclient.return_value.get_signing_key_from_jwt.return_value = SimpleNamespace(key="pub")
            self.assertIsNone(BetterAuthJWTAuthentication().authenticate(_request("Bearer a.b.c")))


def _drf_request(*, user=None, query_email="", body_email=""):
    return SimpleNamespace(
        user=user,
        query_params={"email": query_email} if query_email else {},
        data={"email": body_email} if body_email else {},
    )


class IdentityResolverTests(SimpleTestCase):
    def test_verified_email_from_principal(self):
        req = _drf_request(user=VerifiedUser(email="a@b.com"))
        self.assertEqual(verified_email(req), "a@b.com")
        email, err = resolve_request_email(req)
        self.assertIsNone(err)
        self.assertEqual(email, "a@b.com")

    def test_prefers_verified_over_legacy(self):
        req = _drf_request(user=VerifiedUser(email="real@b.com"), query_email="attacker@evil.com")
        email, err = resolve_request_email(req)
        self.assertEqual(email, "real@b.com")

    def test_legacy_fallback_when_not_enforced(self):
        req = _drf_request(user=None, query_email="Legacy@B.com")
        with override_settings(REQUIRE_VERIFIED_IDENTITY=False):
            email, err = resolve_request_email(req)
        self.assertIsNone(err)
        self.assertEqual(email, "legacy@b.com")

    def test_enforced_rejects_unverified(self):
        req = _drf_request(user=None, query_email="legacy@b.com")
        with override_settings(REQUIRE_VERIFIED_IDENTITY=True):
            email, err = resolve_request_email(req)
        self.assertIsNone(email)
        self.assertIsNotNone(err)
        self.assertEqual(err.status_code, 401)

    def test_per_call_require_verified_overrides_flag(self):
        req = _drf_request(user=None, query_email="legacy@b.com")
        with override_settings(REQUIRE_VERIFIED_IDENTITY=False):
            email, err = resolve_request_email(req, require_verified=True)
        self.assertEqual(err.status_code, 401)

    def test_missing_email_is_400(self):
        req = _drf_request(user=None)
        email, err = resolve_request_email(req)
        self.assertIsNone(email)
        self.assertEqual(err.status_code, 400)
