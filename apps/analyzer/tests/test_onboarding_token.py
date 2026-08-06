"""Single-use semantics of the onboarding token.

The token is the anti-botnet gate on the expensive public endpoints: it is
signed, IP-bound, and consumed on first successful verify. The regression this
covers is that "single-use" was really "single-second". ``signing.dumps``
stamps time at whole-second resolution, so two mints for the same IP inside one
second produced byte-identical tokens; ``_consumed_key`` hashes the token, so
consuming either marked the other consumed. A second browser tab, a retry, or
two people behind one office NAT was enough to 401 a user out of onboarding
with a token that had just been minted for them.

The suite's default DummyCache never stores the consumed marker, which is
exactly why this went unnoticed — these tests pin a real cache backend so the
consumed path is actually exercised.
"""

from unittest.mock import patch

from django.core import signing
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from apps.analyzer.onboarding_security import (
    _SALT,
    consume_token,
    mint_token,
    verify_token,
)

IP = "203.0.113.7"
OTHER_IP = "198.51.100.4"

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class OnboardingTokenSingleUseTests(SimpleTestCase):
    def setUp(self):
        # LocMemCache is process-global; consumed markers would otherwise leak
        # between tests in this class.
        cache.clear()

    def test_two_mints_in_the_same_second_are_distinct(self):
        """Time is frozen so 'same second' is guaranteed, not incidental."""
        with patch("django.core.signing.time.time", return_value=1_800_000_000.0):
            first = mint_token(IP)
            second = mint_token(IP)

        self.assertNotEqual(first, second)

    def test_consuming_one_token_does_not_consume_a_fresh_one(self):
        """The bug: the second mint was rejected as already-consumed."""
        with patch("django.core.signing.time.time", return_value=1_800_000_000.0):
            first = mint_token(IP)
            consume_token(first)
            second = mint_token(IP)

            ok, reason = verify_token(second, IP)

        self.assertTrue(ok, f"a freshly minted token was rejected as {reason!r}")
        self.assertEqual(reason, "")

    def test_a_consumed_token_is_still_rejected_on_replay(self):
        """The property the nonce must not weaken."""
        token = mint_token(IP)
        self.assertTrue(verify_token(token, IP)[0])

        consume_token(token)

        self.assertEqual(verify_token(token, IP), (False, "consumed"))

    def test_a_token_is_still_bound_to_its_ip(self):
        token = mint_token(IP)
        self.assertEqual(verify_token(token, OTHER_IP), (False, "ip_mismatch"))

    def test_tokens_minted_before_the_nonce_existed_still_verify(self):
        """Tokens live 15 minutes, so some are in flight across the deploy."""
        legacy = signing.dumps({"ip": IP}, salt=_SALT)

        self.assertEqual(verify_token(legacy, IP), (True, ""))
