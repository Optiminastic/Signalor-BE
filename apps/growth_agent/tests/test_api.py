"""
Growth Agent API: signed-request auth, Pro/Max gating, per-owner scoping.

Run: python manage.py test apps.growth_agent.tests.test_api --settings=config.settings.test
"""

import os
import time
from unittest import mock

from django.test import override_settings
from rest_framework.test import APITestCase

from apps.accounts.models import Subscription
from apps.organizations.models import Organization

from ..auth import MAX_CLOCK_SKEW_SECONDS, sign_request
from ..models import ChannelMode, GrowthProgram

SECRET = "test-growth-secret"
PRO_EMAIL = "owner@brand-pro.com"
STARTER_EMAIL = "owner@brand-starter.com"
OTHER_EMAIL = "someone@other-brand.com"
ENFORCE_LIMITS = {"ENFORCE_PLAN_LIMITS": "true", "DISABLE_PAYMENT": ""}


@override_settings(GROWTH_AGENT_SIGNING_SECRET=SECRET)
@mock.patch.dict(os.environ, ENFORCE_LIMITS)
class GrowthApiTestCase(APITestCase):
    def setUp(self):
        Subscription.objects.create(email=PRO_EMAIL, plan="pro", status="active")
        self.pro_org = Organization.objects.create(
            name="Pro Brand", url="https://pro.example", owner_email=PRO_EMAIL
        )
        self.other_org = Organization.objects.create(
            name="Other Brand", url="https://other.example", owner_email=OTHER_EMAIL
        )

    def signed(self, method, path, email, *, timestamp=None, secret=SECRET, sign_path=None):
        ts = str(timestamp if timestamp is not None else int(time.time()))
        signature = sign_request(secret, ts, email, method, sign_path or path)
        headers = {
            "HTTP_X_GROWTH_EMAIL": email,
            "HTTP_X_GROWTH_TIMESTAMP": ts,
            "HTTP_X_GROWTH_SIGNATURE": signature,
        }
        call = getattr(self.client, method.lower())
        return (
            call(path, format="json", **headers)
            if method == "GET"
            else call(path, data=self._body, format="json", **headers)
        )

    _body: dict = {}

    def post_program(self, email, org_slug):
        self._body = {"org_slug": org_slug}
        return self.signed("POST", "/api/growth/programs/", email)


class SignatureTests(GrowthApiTestCase):
    def test_unsigned_request_is_rejected(self):
        response = self.client.get("/api/growth/me/")
        self.assertEqual(response.status_code, 401)

    def test_wrong_secret_is_rejected(self):
        response = self.signed("GET", "/api/growth/me/", PRO_EMAIL, secret="wrong")
        self.assertEqual(response.status_code, 401)

    def test_stale_timestamp_is_rejected(self):
        stale = int(time.time()) - MAX_CLOCK_SKEW_SECONDS - 10
        response = self.signed("GET", "/api/growth/me/", PRO_EMAIL, timestamp=stale)
        self.assertEqual(response.status_code, 401)

    def test_signature_cannot_be_retargeted_to_another_path(self):
        response = self.signed("GET", "/api/growth/programs/x/", PRO_EMAIL, sign_path="/api/growth/me/")
        self.assertEqual(response.status_code, 401)

    @override_settings(GROWTH_AGENT_SIGNING_SECRET="")
    def test_unconfigured_secret_rejects_everything(self):
        response = self.signed("GET", "/api/growth/me/", PRO_EMAIL)
        self.assertEqual(response.status_code, 401)


class MeTests(GrowthApiTestCase):
    def test_lists_only_owned_brands(self):
        response = self.signed("GET", "/api/growth/me/", PRO_EMAIL)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["eligible"])
        slugs = [b["slug"] for b in response.data["brands"]]
        self.assertEqual(slugs, [self.pro_org.slug])

    def test_starter_user_sees_not_eligible(self):
        response = self.signed("GET", "/api/growth/me/", STARTER_EMAIL)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["eligible"])
        self.assertEqual(response.data["plan"], "starter")


class ProgramTests(GrowthApiTestCase):
    def test_create_program_defaults_every_channel_to_autonomous(self):
        response = self.post_program(PRO_EMAIL, self.pro_org.slug)
        self.assertEqual(response.status_code, 201)
        modes = set(response.data["channel_modes"].values())
        self.assertEqual(modes, {ChannelMode.AUTONOMOUS.value})
        self.assertEqual(response.data["brand"]["slug"], self.pro_org.slug)

    def test_create_program_is_idempotent(self):
        self.post_program(PRO_EMAIL, self.pro_org.slug)
        response = self.post_program(PRO_EMAIL, self.pro_org.slug)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(GrowthProgram.objects.count(), 1)

    def test_cannot_create_program_for_someone_elses_brand(self):
        response = self.post_program(PRO_EMAIL, self.other_org.slug)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(GrowthProgram.objects.exists())

    def test_cannot_read_someone_elses_program(self):
        GrowthProgram.objects.create(organization=self.other_org, created_by_email=OTHER_EMAIL)
        path = f"/api/growth/programs/{self.other_org.slug}/"
        response = self.signed("GET", path, PRO_EMAIL)
        self.assertEqual(response.status_code, 404)

    def test_starter_plan_is_forbidden(self):
        starter_org = Organization.objects.create(
            name="Starter", url="https://starter.example", owner_email=STARTER_EMAIL
        )
        response = self.post_program(STARTER_EMAIL, starter_org.slug)
        self.assertEqual(response.status_code, 403)

    def test_reads_own_program(self):
        self.post_program(PRO_EMAIL, self.pro_org.slug)
        path = f"/api/growth/programs/{self.pro_org.slug}/"
        response = self.signed("GET", path, PRO_EMAIL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], GrowthProgram.Status.ONBOARDING)
