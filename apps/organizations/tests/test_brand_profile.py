"""Tests for the BrandProfile bootstrap service (Epic 2). LLM + brand_kit are mocked."""

from unittest.mock import patch

from django.test import TestCase

from apps.analyzer.models import AnalysisRun
from apps.organizations.models import BrandProfile, Organization
from apps.organizations.schemas import BrandIdentity, BrandSynthesis
from apps.organizations.services import brand_profile

_KIT = "apps.analyzer.services.brand_kit.get_or_generate"
_SYNTH = "apps.analyzer.pipeline.structured.ask_structured"


class BootstrapTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", url="https://acme.com", owner_email="o@x.com")
        self.run = AnalysisRun.objects.create(
            url="https://acme.com", brand_name="Acme", organization=self.org
        )

    def test_null_org_returns_none(self):
        run_no_org = AnalysisRun.objects.create(url="https://x.com")
        self.assertIsNone(brand_profile.bootstrap_from_run(run_no_org))
        self.assertEqual(BrandProfile.objects.count(), 0)

    @patch(_KIT)
    @patch(_SYNTH)
    def test_creates_pending_profile(self, mock_synth, mock_kit):
        mock_kit.return_value = {
            "name": "Acme",
            "url": "https://acme.com",
            "tagline": "T",
            "categories": ["Widgets"],
        }
        mock_synth.return_value = BrandSynthesis(identity=BrandIdentity(name="Acme", industry="Widgets"))
        profile = brand_profile.bootstrap_from_run(self.run, market_profile={})
        self.assertIsNotNone(profile)
        self.assertEqual(profile.status, BrandProfile.Status.PENDING)
        self.assertEqual(profile.identity["name"], "Acme")
        self.assertEqual(profile.identity["url"], "https://acme.com")  # deterministic
        self.assertTrue(profile.sources["brand_kit"])
        self.assertEqual(profile.source_run_id, self.run.pk)

    @patch(_KIT)
    @patch(_SYNTH)
    def test_synthesis_failure_seeds_identity_from_kit(self, mock_synth, mock_kit):
        mock_kit.return_value = {"name": "Acme", "tagline": "Fallback tagline"}
        mock_synth.return_value = None  # LLM synthesis failed
        profile = brand_profile.bootstrap_from_run(self.run, market_profile={})
        self.assertEqual(profile.identity["tagline"], "Fallback tagline")
        self.assertEqual(profile.positioning, {})

    @patch(_KIT)
    @patch(_SYNTH)
    def test_does_not_clobber_approved(self, mock_synth, mock_kit):
        mock_kit.return_value = {}
        mock_synth.return_value = None
        approved = BrandProfile.objects.create(
            organization=self.org,
            status=BrandProfile.Status.APPROVED,
            identity={"name": "Human-edited"},
        )
        profile = brand_profile.bootstrap_from_run(self.run, market_profile={})
        self.assertEqual(profile.pk, approved.pk)
        self.assertEqual(profile.identity["name"], "Human-edited")
        mock_synth.assert_not_called()  # short-circuited before the LLM

    @patch(_KIT)
    @patch(_SYNTH)
    def test_idempotent_upsert(self, mock_synth, mock_kit):
        mock_kit.return_value = {"name": "Acme"}
        mock_synth.return_value = None
        p1 = brand_profile.bootstrap_from_run(self.run, market_profile={})
        p2 = brand_profile.bootstrap_from_run(self.run, market_profile={})
        self.assertEqual(p1.pk, p2.pk)
        self.assertEqual(BrandProfile.objects.filter(organization=self.org).count(), 1)
