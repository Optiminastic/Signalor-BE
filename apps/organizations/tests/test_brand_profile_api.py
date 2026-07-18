"""Tests for the BrandProfile DRF endpoints (Epic 2): owner-scoping + review flow."""

import json

from django.test import TestCase

from apps.organizations.models import BrandProfile, Organization


class BrandProfileApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", url="https://acme.com", owner_email="owner@x.com")
        self.profile = BrandProfile.objects.create(
            organization=self.org,
            status=BrandProfile.Status.PENDING,
            identity={"name": "Acme"},
        )
        self.base = f"/api/organizations/{self.org.slug}/brand-profile/"

    def test_get_as_owner(self):
        resp = self.client.get(self.base, {"email": "owner@x.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["identity"]["name"], "Acme")

    def test_get_as_non_owner_is_404(self):
        resp = self.client.get(self.base, {"email": "someone-else@x.com"})
        self.assertEqual(resp.status_code, 404)

    def test_get_without_email_is_404(self):
        self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_patch_edits_sections_only(self):
        resp = self.client.patch(
            self.base,
            data=json.dumps(
                {"email": "owner@x.com", "identity": {"name": "Acme Corp"}, "status": "approved"}
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.identity["name"], "Acme Corp")
        # status is read-only over PATCH -> ignored
        self.assertEqual(self.profile.status, BrandProfile.Status.PENDING)

    def test_review_approve_stamps_verified(self):
        resp = self.client.post(
            self.base + "review/",
            data=json.dumps({"email": "owner@x.com", "decision": "approve"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, BrandProfile.Status.APPROVED)
        self.assertIsNotNone(self.profile.last_verified_at)

    def test_review_bad_decision_is_400(self):
        resp = self.client.post(
            self.base + "review/",
            data=json.dumps({"email": "owner@x.com", "decision": "maybe"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_review_as_non_owner_is_404_and_no_change(self):
        resp = self.client.post(
            self.base + "review/",
            data=json.dumps({"email": "evil@x.com", "decision": "approve"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, BrandProfile.Status.PENDING)
