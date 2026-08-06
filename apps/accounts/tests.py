"""Project (brand) cap behaviour in ``subscription_utils``.

Regression cover for the "Max (Internal) account cannot add a second brand" bug:
the individual single-brand invariant was applied before the internal-email
bypass, so an @optiminastic.com workspace was capped at one brand even though
``is_internal_email`` grants business-tier access.
"""

import os
from unittest import mock

from django.test import TestCase, override_settings

from apps.accounts.models import (
    AGENCY_MAX_PROJECTS,
    AGENCY_UNPAID_MAX_PROJECTS,
    PLAN_LIMITS,
    AccountProfile,
    Subscription,
)
from apps.accounts.subscription_utils import effective_max_projects, project_limit_reached
from apps.organizations.models import Organization

INTERNAL = "devops@optiminastic.com"
CUSTOMER = "owner@acme-brand.com"


def _make_org(email: str, name: str) -> Organization:
    return Organization.objects.create(name=name, url=f"https://{name}.com", owner_email=email)


def _make_agency(email: str = CUSTOMER) -> AccountProfile:
    return AccountProfile.objects.create(
        email=email, account_type=AccountProfile.AccountType.AGENCY
    )


def _make_sub(email: str, plan: str, *, active: bool = True) -> Subscription:
    return Subscription.objects.create(
        email=email,
        plan=plan,
        status=Subscription.Status.ACTIVE if active else Subscription.Status.CANCELED,
    )


class EffectiveMaxProjectsTests(TestCase):
    def test_internal_email_is_not_capped_at_one(self):
        """The bug: internal accounts were folded into the individual cap."""
        self.assertEqual(effective_max_projects(INTERNAL), AGENCY_MAX_PROJECTS)

    def test_individual_customer_is_capped_at_one(self):
        self.assertEqual(effective_max_projects(CUSTOMER), 1)

    def test_individual_customer_is_capped_at_one_even_when_paying(self):
        _make_sub(CUSTOMER, "pro")
        self.assertEqual(effective_max_projects(CUSTOMER), 1)

    def test_unpaid_agency_does_not_get_a_roster(self):
        """Regression: an agency with no Subscription row fell back to starter
        limits and was then handed the flat 1000-brand ceiling."""
        _make_agency()
        self.assertEqual(effective_max_projects(CUSTOMER), AGENCY_UNPAID_MAX_PROJECTS)
        self.assertLess(effective_max_projects(CUSTOMER), AGENCY_MAX_PROJECTS)

    def test_agency_with_inactive_subscription_is_treated_as_unpaid(self):
        _make_agency()
        _make_sub(CUSTOMER, "pro", active=False)
        self.assertEqual(effective_max_projects(CUSTOMER), AGENCY_UNPAID_MAX_PROJECTS)

    def test_paid_agency_gets_its_plan_allowance(self):
        _make_agency()
        _make_sub(CUSTOMER, "agency")
        self.assertEqual(
            effective_max_projects(CUSTOMER), PLAN_LIMITS["agency"]["max_agency_projects"]
        )

    def test_agency_allowance_scales_with_plan(self):
        _make_agency()
        _make_sub(CUSTOMER, "pro")
        self.assertEqual(
            effective_max_projects(CUSTOMER), PLAN_LIMITS["pro"]["max_agency_projects"]
        )
        self.assertGreater(
            PLAN_LIMITS["pro"]["max_agency_projects"],
            PLAN_LIMITS["agency"]["max_agency_projects"],
        )

    def test_grandfathered_business_agency_stays_uncapped(self):
        """business declares max_agency_projects=0, which means uncapped."""
        _make_agency()
        _make_sub(CUSTOMER, "business")
        self.assertEqual(effective_max_projects(CUSTOMER), AGENCY_MAX_PROJECTS)

    def test_declaring_agency_on_a_single_brand_plan_grants_nothing(self):
        """The escalation this closes.

        ``account_type`` is a free, self-service field. Before, an active
        single-brand customer could POST /api/account/type/ {"account_type":
        "agency"} and jump from 1 brand to 5 without paying a penny more,
        because the allowance was read off the plan's ``max_agency_projects``
        as soon as the account was typed as an agency.
        """
        _make_agency()
        _make_sub(CUSTOMER, "starter")
        self.assertEqual(effective_max_projects(CUSTOMER), PLAN_LIMITS["starter"]["max_projects"])
        self.assertEqual(effective_max_projects(CUSTOMER), 1)

    def test_the_roster_arrives_when_the_agency_plan_is_bought(self):
        """Same account, after actually buying the agency plan."""
        _make_agency()
        sub = _make_sub(CUSTOMER, "starter")
        self.assertEqual(effective_max_projects(CUSTOMER), 1)

        sub.plan = "agency"
        sub.save(update_fields=["plan"])
        self.assertEqual(
            effective_max_projects(CUSTOMER), PLAN_LIMITS["agency"]["max_agency_projects"]
        )


@override_settings(DEBUG=False)
@mock.patch.dict(os.environ, {"DISABLE_PAYMENT": "false", "ENFORCE_PLAN_LIMITS": "true"})
class AgencyProjectLimitTests(TestCase):
    """Cap enforcement for agencies.

    The env is pinned because the repo's local ``.env`` sets DISABLE_PAYMENT=true,
    which switches every plan cap off — without this the assertions would pass
    vacuously against an unenforced code path.
    """

    def test_unpaid_agency_blocked_after_its_free_brand(self):
        _make_agency()
        for i in range(AGENCY_UNPAID_MAX_PROJECTS):
            _make_org(CUSTOMER, f"free{i}")
        reached, msg = project_limit_reached(CUSTOMER)
        self.assertTrue(reached)
        # Must not name a plan the account does not have.
        self.assertNotIn("Self-Serve Brand", msg)
        self.assertIn("Subscribe", msg)

    def test_paid_agency_blocked_at_its_plan_allowance(self):
        _make_agency()
        _make_sub(CUSTOMER, "agency")
        allowance = PLAN_LIMITS["agency"]["max_agency_projects"]
        for i in range(allowance - 1):
            _make_org(CUSTOMER, f"brand{i}")
        self.assertFalse(project_limit_reached(CUSTOMER)[0])

        _make_org(CUSTOMER, "last")
        reached, msg = project_limit_reached(CUSTOMER)
        self.assertTrue(reached)
        self.assertIn(str(allowance), msg)

    def test_agency_on_a_single_brand_plan_is_told_to_switch_plan(self):
        """Not the generic prompts/support upgrade hint — they need the roster."""
        _make_agency()
        _make_sub(CUSTOMER, "starter")
        _make_org(CUSTOMER, "only")

        reached, msg = project_limit_reached(CUSTOMER)
        self.assertTrue(reached)
        self.assertIn("Agency plan", msg)
        self.assertNotIn("Managed Growth", msg)


class ProjectLimitReachedTests(TestCase):
    def test_internal_email_may_add_a_second_brand(self):
        _make_org(INTERNAL, "first")
        reached, msg = project_limit_reached(INTERNAL)
        self.assertFalse(reached)
        self.assertEqual(msg, "")

    def test_individual_customer_blocked_after_first_brand(self):
        _make_org(CUSTOMER, "first")
        reached, msg = project_limit_reached(CUSTOMER)
        self.assertTrue(reached)
        self.assertIn("Agency", msg)

    def test_individual_customer_may_create_their_first_brand(self):
        reached, _ = project_limit_reached(CUSTOMER)
        self.assertFalse(reached)

    def test_missing_email_is_rejected(self):
        reached, msg = project_limit_reached("")
        self.assertTrue(reached)
        self.assertEqual(msg, "Email is required.")


class AgencyCheckoutTests(TestCase):
    """Agency accounts check out with the agency-priced product (or the
    fallback discount code), Individuals with the standard one."""

    AGENCY = "ops@some-agency.com"
    ENV = {
        "DODO_PRODUCT_ID_STARTER": "pdt_individual_starter",
        "DODO_PRODUCT_ID_AGENCY_STARTER": "",
        "DODO_AGENCY_DISCOUNT_CODE": "",
    }

    def _checkout(self, email):
        from unittest.mock import MagicMock

        dodo = MagicMock()
        dodo.checkout_sessions.create.return_value = MagicMock(checkout_url="https://dodo/x")
        with mock.patch("apps.accounts.views._get_dodo", return_value=dodo):
            resp = self.client.post(
                "/api/payments/create-checkout/",
                {"email": email, "plan": "starter"},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        return dodo.checkout_sessions.create.call_args.kwargs

    def _make_agency(self):
        AccountProfile.objects.create(email=self.AGENCY, account_type="agency")

    def test_agency_gets_the_agency_priced_product(self):
        self._make_agency()
        env = {**self.ENV, "DODO_PRODUCT_ID_AGENCY_STARTER": "pdt_agency_starter"}
        with mock.patch.dict(os.environ, env):
            kwargs = self._checkout(self.AGENCY)
        self.assertEqual(kwargs["product_cart"][0]["product_id"], "pdt_agency_starter")
        # Price is already agency-priced — no extra discount stacking.
        self.assertNotIn("discount_code", kwargs)
        # Webhooks map the plan from metadata, independent of the product.
        self.assertEqual(kwargs["metadata"]["plan"], "starter")

    def test_agency_without_agency_product_falls_back_to_discount_code(self):
        self._make_agency()
        env = {**self.ENV, "DODO_AGENCY_DISCOUNT_CODE": "AGENCY15"}
        with mock.patch.dict(os.environ, env):
            kwargs = self._checkout(self.AGENCY)
        self.assertEqual(kwargs["product_cart"][0]["product_id"], "pdt_individual_starter")
        self.assertEqual(kwargs["discount_code"], "AGENCY15")

    def test_individual_gets_the_standard_product_with_no_agency_discount(self):
        env = {
            **self.ENV,
            "DODO_PRODUCT_ID_AGENCY_STARTER": "pdt_agency_starter",
            "DODO_AGENCY_DISCOUNT_CODE": "AGENCY15",
        }
        with mock.patch.dict(os.environ, env):
            kwargs = self._checkout(CUSTOMER)
        self.assertEqual(kwargs["product_cart"][0]["product_id"], "pdt_individual_starter")
        self.assertNotIn("discount_code", kwargs)


class VerifyCheckoutTests(TestCase):
    """Webhook-independent activation: Dodo's record is the only trusted input."""

    EMAIL = "payer@brand.com"
    SUB_ID = "sub_dodo_123"

    def _remote(self, status="active", email=None, plan="starter"):
        remote = mock.MagicMock()
        remote.status = status
        remote.subscription_id = self.SUB_ID
        remote.customer = mock.MagicMock()
        remote.customer.email = email if email is not None else self.EMAIL
        remote.customer.customer_id = "cus_1"
        remote.metadata = {"email": email if email is not None else self.EMAIL, "plan": plan}
        remote.next_billing_date = ""
        return remote

    def _verify(self, remote, email=None, subscription_id=None):
        dodo = mock.MagicMock()
        dodo.subscriptions.retrieve.return_value = remote
        with mock.patch("apps.accounts.views._get_dodo", return_value=dodo):
            return self.client.post(
                "/api/payments/verify-checkout/",
                {"email": email or self.EMAIL, "subscription_id": subscription_id or self.SUB_ID},
                content_type="application/json",
            )

    def test_active_dodo_subscription_activates_locally(self):
        resp = self._verify(self._remote())
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["is_active"])
        sub = Subscription.objects.get(email=self.EMAIL)
        self.assertEqual(sub.status, "active")
        self.assertEqual(sub.plan, "starter")
        self.assertEqual(sub.payment_subscription_id, self.SUB_ID)

    def test_pending_dodo_subscription_does_not_activate(self):
        resp = self._verify(self._remote(status="pending"))
        self.assertFalse(resp.json()["is_active"])
        sub = Subscription.objects.filter(email=self.EMAIL).first()
        self.assertTrue(sub is None or not sub.is_active)

    def test_someone_elses_subscription_id_cannot_activate_the_caller(self):
        resp = self._verify(self._remote(email="victim@other.com"), email=self.EMAIL)
        self.assertFalse(resp.json()["is_active"])
        self.assertFalse(Subscription.objects.filter(email=self.EMAIL, status="active").exists())
        # And the real payer was not activated on the attacker's behalf either.
        self.assertFalse(Subscription.objects.filter(email="victim@other.com").exists())

    def test_already_active_short_circuits_without_calling_dodo(self):
        Subscription.objects.create(email=self.EMAIL, status="active")
        with mock.patch("apps.accounts.views._get_dodo") as get_dodo:
            resp = self.client.post(
                "/api/payments/verify-checkout/",
                {"email": self.EMAIL, "subscription_id": self.SUB_ID},
                content_type="application/json",
            )
        self.assertTrue(resp.json()["is_active"])
        get_dodo.assert_not_called()
