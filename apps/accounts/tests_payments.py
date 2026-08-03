"""Payment path cover: checkout session creation and the Dodo webhook.

The webhook is what actually grants paid access — it is the only thing that
flips a Subscription to ``active`` and sets the plan. It had no tests, so a
signature regression or a metadata rename would have shipped silently and
either locked out paying customers or granted access without payment.
"""

import base64
import hashlib
import hmac
import json
import os
import time
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import AccountProfile, Subscription
from apps.accounts.views import WEBHOOK_SIGNATURE_MAX_AGE_SEC
from apps.organizations.models import Organization

WEBHOOK_SECRET = "whsec_" + base64.b64encode(b"test-secret-material").decode()
BUYER = "buyer@acme-brand.com"
INTERNAL = "devops@optiminastic.com"
AGENCY = "owner@acme-agency.com"


def _sign(body: bytes, *, msg_id: str = "msg_1", timestamp: str | None = None) -> dict:
    """Standard Webhooks signature headers for ``body``.

    Defaults to *now* because the handler enforces a freshness window; a fixed
    historical stamp would make every test fail replay protection.
    """
    if timestamp is None:
        timestamp = str(int(time.time()))
    secret_bytes = base64.b64decode(WEBHOOK_SECRET[len("whsec_") :])
    to_sign = f"{msg_id}.{timestamp}.{body.decode()}"
    digest = hmac.new(secret_bytes, to_sign.encode(), hashlib.sha256).digest()
    return {
        "HTTP_WEBHOOK_ID": msg_id,
        "HTTP_WEBHOOK_TIMESTAMP": timestamp,
        "HTTP_WEBHOOK_SIGNATURE": "v1," + base64.b64encode(digest).decode(),
    }


def _subscription_active_event(email: str = BUYER, plan: str = "pro") -> bytes:
    return json.dumps(
        {
            "type": "subscription.active",
            "data": {
                "subscription_id": "sub_123",
                "customer": {"customer_id": "cus_123", "email": email},
                "metadata": {"email": email, "plan": plan},
                "next_billing_date": "2026-09-01T00:00:00Z",
            },
        }
    ).encode()


@mock.patch.dict(os.environ, {"DODO_WEBHOOK_SECRET": WEBHOOK_SECRET})
class DodoWebhookSignatureTests(TestCase):
    """The webhook must fail closed on anything it cannot verify."""

    def setUp(self):
        self.url = reverse("accounts:dodo-webhook")

    def test_valid_signature_activates_the_subscription(self):
        body = _subscription_active_event()
        resp = self.client.post(
            self.url, body, content_type="application/json", **_sign(body)
        )
        self.assertEqual(resp.status_code, 200)
        sub = Subscription.objects.get(email=BUYER)
        self.assertTrue(sub.is_active)

    def test_plan_comes_from_metadata_not_a_default(self):
        """Buying 'pro' must not silently grant 'starter' limits."""
        body = _subscription_active_event(plan="pro")
        self.client.post(self.url, body, content_type="application/json", **_sign(body))
        self.assertEqual(Subscription.objects.get(email=BUYER).plan, "pro")

    def test_unknown_plan_in_metadata_is_ignored(self):
        """A forged/renamed plan must not create an unknown tier."""
        body = _subscription_active_event(plan="enterprise-unlimited")
        self.client.post(self.url, body, content_type="application/json", **_sign(body))
        self.assertIn(
            Subscription.objects.get(email=BUYER).plan,
            ("starter", "agency", "pro", "business"),
        )

    def test_agency_purchase_activates_on_the_agency_plan(self):
        """An agency buyer must not silently land on the brand plan."""
        body = _subscription_active_event(plan="agency")
        self.client.post(self.url, body, content_type="application/json", **_sign(body))
        self.assertEqual(Subscription.objects.get(email=BUYER).plan, "agency")

    def test_tampered_body_is_rejected(self):
        body = _subscription_active_event()
        headers = _sign(body)
        forged = _subscription_active_event(email="attacker@evil.com")
        resp = self.client.post(
            self.url, forged, content_type="application/json", **headers
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Subscription.objects.filter(email="attacker@evil.com").exists())

    def test_missing_signature_headers_are_rejected(self):
        body = _subscription_active_event()
        resp = self.client.post(self.url, body, content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Subscription.objects.filter(email=BUYER).exists())

    def test_wrong_secret_is_rejected(self):
        body = _subscription_active_event()
        headers = _sign(body)
        bad = base64.b64encode(
            hmac.new(b"other-secret", b"whatever", hashlib.sha256).digest()
        ).decode()
        headers["HTTP_WEBHOOK_SIGNATURE"] = "v1," + bad
        resp = self.client.post(
            self.url, body, content_type="application/json", **headers
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Subscription.objects.filter(email=BUYER).exists())


@mock.patch.dict(os.environ, {"DODO_WEBHOOK_SECRET": WEBHOOK_SECRET})
class DodoWebhookReplayTests(TestCase):
    """A correctly signed request must still expire."""

    def setUp(self):
        self.url = reverse("accounts:dodo-webhook")

    def _post_at(self, timestamp: str):
        body = _subscription_active_event()
        return self.client.post(
            self.url,
            body,
            content_type="application/json",
            **_sign(body, timestamp=timestamp),
        )

    def test_replayed_old_request_is_rejected(self):
        stale = str(int(time.time()) - WEBHOOK_SIGNATURE_MAX_AGE_SEC - 60)
        self.assertEqual(self._post_at(stale).status_code, 400)
        self.assertFalse(Subscription.objects.filter(email=BUYER).exists())

    def test_far_future_timestamp_is_rejected(self):
        ahead = str(int(time.time()) + WEBHOOK_SIGNATURE_MAX_AGE_SEC + 60)
        self.assertEqual(self._post_at(ahead).status_code, 400)

    def test_non_numeric_timestamp_is_rejected(self):
        self.assertEqual(self._post_at("not-a-timestamp").status_code, 400)

    def test_small_clock_skew_still_succeeds(self):
        """Provider retries and modest skew must not lock out real payments."""
        skewed = str(int(time.time()) - 60)
        self.assertEqual(self._post_at(skewed).status_code, 200)


@mock.patch.dict(os.environ, {"DODO_WEBHOOK_SECRET": ""})
class DodoWebhookUnconfiguredTests(TestCase):
    def test_missing_secret_rejects_rather_than_trusting_the_caller(self):
        body = _subscription_active_event()
        resp = self.client.post(
            reverse("accounts:dodo-webhook"), body, content_type="application/json", **_sign(body)
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Subscription.objects.filter(email=BUYER).exists())


class CreateCheckoutSessionTests(TestCase):
    """Only the two self-serve plans may be bought without talking to sales."""

    def setUp(self):
        self.url = reverse("accounts:create-checkout")

    def _post(self, **payload):
        return self.client.post(self.url, payload, content_type="application/json")

    def test_email_is_required(self):
        self.assertEqual(self._post(plan="starter").status_code, 400)

    def test_agency_plan_is_not_self_serve(self):
        resp = self._post(email=BUYER, plan="agency-brand-10")
        self.assertEqual(resp.status_code, 400)

    def test_enterprise_is_not_self_serve(self):
        self.assertEqual(self._post(email=BUYER, plan="enterprise").status_code, 400)

    def test_retired_pro_plan_is_not_sellable(self):
        """Only the brand and agency plans are sold; pro is grandfathered."""
        self.assertEqual(self._post(email=BUYER, plan="pro").status_code, 400)

    @mock.patch.dict(os.environ, {"DODO_PRODUCT_ID_AGENCY": ""}, clear=False)
    @mock.patch("apps.accounts.views._get_dodo")
    def test_agency_plan_is_sellable(self, get_dodo):
        """Reaching the product lookup proves 'agency' passed the allowlist."""
        get_dodo.return_value = object()
        resp = self._post(email=BUYER, plan="agency")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("agency", resp.json().get("error", "").lower())

    def test_retired_business_plan_is_not_sellable(self):
        """'business' is grandfathered-only and must not be purchasable."""
        self.assertEqual(self._post(email=BUYER, plan="business").status_code, 400)

    @mock.patch.dict(os.environ, {"DODO_PRODUCT_ID_STARTER": ""}, clear=False)
    @mock.patch("apps.accounts.views._get_dodo")
    def test_unconfigured_product_fails_loudly(self, get_dodo):
        get_dodo.return_value = object()
        resp = self._post(email=BUYER, plan="starter")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("not configured", resp.json().get("error", "").lower())


@mock.patch.dict(os.environ, {"DISABLE_PAYMENT": "false", "ENFORCE_PLAN_LIMITS": "true"})
class UsageCapDisplayTests(TestCase):
    def _usage(self, email):
        r = self.client.get(reverse("accounts:usage"), {"email": email})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_internal_reports_unlimited_not_the_sentinel(self):
        Organization.objects.create(name="S", url="https://s.ai", owner_email=INTERNAL)
        d = self._usage(INTERNAL)
        self.assertEqual(d["limits"]["max_projects"], 0)
        self.assertEqual(d["limits"]["max_prompts"], 0)
        self.assertFalse(d["at_limit"]["projects"])
        self.assertFalse(d["at_limit"]["prompts"])

    def test_paid_agency_reports_its_real_plan_cap(self):
        AccountProfile.objects.create(email=AGENCY, account_type=AccountProfile.AccountType.AGENCY)
        Subscription.objects.create(email=AGENCY, plan="starter", status=Subscription.Status.ACTIVE)
        d = self._usage(AGENCY)
        self.assertEqual(d["limits"]["max_projects"], 5)
        self.assertLess(d["limits"]["max_projects"], 1000)
