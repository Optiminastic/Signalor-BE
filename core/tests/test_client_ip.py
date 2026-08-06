"""Client-IP resolution and the rate-limit bucket it feeds.

``_client_ip`` used to trust ``X-Forwarded-For`` from any peer whenever
``TRUSTED_PROXY_IPS`` was unset - which is the default, and which the setting's
own comment described as "loopback only". Adding one header therefore bought a
fresh rate-limit bucket per request, so a single host could send unlimited
traffic against a per-IP cap. That silently defeated the global limiter, the
per-IP DRF throttles, and the IP binding on onboarding tokens.

Address choice matters here: Python's ``ip_address(...).is_private`` is True for
the TEST-NET documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24),
so those cannot stand in for a public attacker. These tests use real public
addresses instead.
"""

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from core.permissions.middleware import GlobalIPRateLimitMiddleware, _client_ip

PUBLIC_PEER = "93.184.216.34"  # genuinely public, not a documentation range
PROXY_PEER = "172.18.0.5"  # container network, i.e. our reverse proxy
REAL_CLIENT = "8.8.8.8"

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


class ClientIpTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_a_public_peer_cannot_declare_its_own_ip(self):
        """The regression: this returned the spoofed 1.2.3.4."""
        request = self.rf.get("/", REMOTE_ADDR=PUBLIC_PEER, HTTP_X_FORWARDED_FOR="1.2.3.4")

        self.assertEqual(_client_ip(request), PUBLIC_PEER)

    def test_a_proxy_peer_is_still_believed(self):
        """Prod sits behind a proxy; without this every user shares one bucket."""
        request = self.rf.get("/", REMOTE_ADDR=PROXY_PEER, HTTP_X_FORWARDED_FOR=REAL_CLIENT)

        self.assertEqual(_client_ip(request), REAL_CLIENT)

    def test_cf_connecting_ip_wins_over_a_spoofed_forwarded_for(self):
        """Cloudflare appends to the client's XFF, so its leftmost entry is
        attacker-chosen; CF-Connecting-IP is overwritten and so is not."""
        request = self.rf.get(
            "/",
            REMOTE_ADDR=PROXY_PEER,
            HTTP_X_FORWARDED_FOR=f"1.2.3.4, {REAL_CLIENT}",
            HTTP_CF_CONNECTING_IP=REAL_CLIENT,
        )

        self.assertEqual(_client_ip(request), REAL_CLIENT)

    def test_non_ip_junk_never_becomes_a_cache_key(self):
        """The value is interpolated into a cache key and a throttle scope."""
        request = self.rf.get("/", REMOTE_ADDR=PROXY_PEER, HTTP_X_FORWARDED_FOR="not-an-ip")

        self.assertEqual(_client_ip(request), PROXY_PEER)

    @override_settings(TRUSTED_PROXY_IPS={PROXY_PEER})
    def test_an_explicit_allowlist_excludes_other_private_peers(self):
        request = self.rf.get("/", REMOTE_ADDR="10.9.9.9", HTTP_X_FORWARDED_FOR=REAL_CLIENT)

        self.assertEqual(_client_ip(request), "10.9.9.9")


@override_settings(
    CACHES=LOCMEM,
    IP_RATE_LIMIT_ENABLED=True,
    IP_RATE_LIMIT_PER_MINUTE=5,
    IP_RATE_LIMIT_BURST=5,
)
class RateLimitSpoofingTests(SimpleTestCase):
    """The limiter buckets on _client_ip, so spoofing it defeats the limiter."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.rf = RequestFactory()
        self.mw = GlobalIPRateLimitMiddleware(lambda _r: HttpResponse("ok"))

    def _hammer(self, count: int, **headers) -> int:
        blocked = 0
        for _ in range(count):
            request = self.rf.get("/api/analyzer/anything", **headers)
            if self.mw(request).status_code == 429:
                blocked += 1
        return blocked

    def test_rotating_forwarded_for_no_longer_buys_fresh_buckets(self):
        blocked = 0
        for i in range(30):
            request = self.rf.get(
                "/api/analyzer/anything",
                REMOTE_ADDR=PUBLIC_PEER,
                HTTP_X_FORWARDED_FOR=f"10.0.0.{i}",
            )
            if self.mw(request).status_code == 429:
                blocked += 1

        # Before the fix this was 0 of 30 against a cap of 5/min.
        self.assertEqual(blocked, 25)

    def test_a_plain_flood_is_still_capped(self):
        self.assertEqual(self._hammer(30, REMOTE_ADDR=PUBLIC_PEER), 25)

    def test_distinct_real_users_behind_the_proxy_are_not_lumped_together(self):
        """Guards the deployment risk: collapsing everyone into the proxy's
        bucket would rate-limit the whole site."""
        blocked = 0
        for i in range(30):
            request = self.rf.get(
                "/api/analyzer/anything",
                REMOTE_ADDR=PROXY_PEER,
                HTTP_CF_CONNECTING_IP=f"8.8.{i}.1",
            )
            if self.mw(request).status_code == 429:
                blocked += 1

        self.assertEqual(blocked, 0)
