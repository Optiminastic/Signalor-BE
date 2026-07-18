"""Brand-card caching + invalidation (Epic 7)."""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.analyzer._cache import brand_card_key
from apps.organizations.models import BrandProfile, Organization
from apps.organizations.services import brand_context

_APPROVED = "apps.organizations.services.brand_context._approved_profile"

# The suite disables caching (DummyCache) for determinism; this module needs a real
# backend to exercise the cache + invalidation path.
_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=_LOCMEM)
class BrandCardCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.org = Organization.objects.create(name="Acme", url="https://acme.com", owner_email="o@x.com")
        self.profile = BrandProfile.objects.create(
            organization=self.org,
            status=BrandProfile.Status.APPROVED,
            identity={"name": "Acme", "industry": "Widgets"},
        )

    def test_profile_is_only_looked_up_once_across_calls(self):
        with patch(_APPROVED, wraps=brand_context._approved_profile) as spy:
            first = brand_context.build_context(self.org)
            second = brand_context.build_context(self.org)
        self.assertEqual(first, second)
        self.assertIn("Acme", first)
        self.assertEqual(spy.call_count, 1, "second call should be served from cache")

    def test_cache_is_populated_under_the_org_key(self):
        brand_context.build_context(self.org)
        self.assertIsNotNone(cache.get(brand_card_key(self.org.pk)))

    def test_saving_the_profile_invalidates_the_card(self):
        brand_context.build_context(self.org)
        self.assertIsNotNone(cache.get(brand_card_key(self.org.pk)))
        self.profile.identity = {"name": "Acme Renamed", "industry": "Widgets"}
        self.profile.save()  # post_save signal must drop the cached card
        self.assertIsNone(cache.get(brand_card_key(self.org.pk)))
        self.assertIn("Acme Renamed", brand_context.build_context(self.org))

    def test_deleting_the_profile_invalidates_the_card(self):
        brand_context.build_context(self.org)
        self.profile.delete()
        self.assertIsNone(cache.get(brand_card_key(self.org.pk)))

    def test_unapproved_profile_is_not_served_from_cache(self):
        # The only-approved guarantee must survive caching.
        self.profile.status = BrandProfile.Status.PENDING
        self.profile.save()
        out = brand_context.build_context(self.org)
        self.assertNotIn("verified brand facts", out)

    def test_budgeting_still_applies_to_cached_blocks(self):
        brand_context.build_context(self.org)  # warm the cache
        tiny = brand_context.build_context(self.org, max_chars=120)
        self.assertLessEqual(len(tiny), 120)
