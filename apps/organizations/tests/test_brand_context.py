"""Tests for the BrandProfile render + build_context service (Epic 2)."""

from django.test import TestCase

from apps.analyzer.models import AnalysisRun
from apps.organizations.models import BrandProfile, Organization
from apps.organizations.services import brand_context


class BrandContextTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", url="https://acme.com", owner_email="o@x.com")
        self.run = AnalysisRun.objects.create(
            url="https://acme.com", brand_name="Acme", country="India", organization=self.org
        )

    def _profile(self, status):
        return BrandProfile.objects.create(
            organization=self.org,
            status=status,
            identity={
                "name": "Acme",
                "url": "https://acme.com",
                "tagline": "We build things",
                "short_description": "Acme makes widgets.",
            },
            canonical_facts={"country": "India", "currencies": ["INR"], "contact_email": "o@x.com"},
            positioning={"category": "Widgets", "one_liner": "Best widgets"},
            audience={"primary_segment": "SMBs"},
            voice={"tone": ["friendly"]},
            competitors=[{"name": "Rivalco"}],
        )

    def test_render_contains_identity_and_orders_before_competitors(self):
        card = brand_context.render_brand_card(self._profile(BrandProfile.Status.APPROVED))
        self.assertIn("Acme", card)
        self.assertIn("India", card)
        self.assertLess(card.index("Identity"), card.index("Competitors"))

    def test_only_approved_feeds_prompts(self):
        # A PENDING profile must NOT feed the prompt; falls back to the ephemeral card.
        self._profile(BrandProfile.Status.PENDING)
        ctx = brand_context.build_context(self.run)
        self.assertNotIn("verified brand facts", ctx)
        self.assertIn("Acme", ctx)

    def test_approved_profile_is_used(self):
        self._profile(BrandProfile.Status.APPROVED)
        ctx = brand_context.build_context(self.run)
        self.assertIn("verified brand facts", ctx)
        self.assertIn("Widgets", ctx)

    def test_ephemeral_when_no_profile(self):
        ctx = brand_context.build_context(self.run)
        self.assertIn("Acme", ctx)
        self.assertIn("basic, unverified", ctx)

    def test_budget_keeps_identity(self):
        profile = self._profile(BrandProfile.Status.APPROVED)
        profile.voice = {"tone": ["x" * 5000]}  # huge low-priority section
        profile.save()
        ctx = brand_context.build_context(self.run, max_chars=300)
        self.assertLessEqual(len(ctx), 300)
        self.assertIn("Acme", ctx)  # identity survived the budget
