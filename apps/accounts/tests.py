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
        _make_sub(CUSTOMER, "starter")
        self.assertEqual(
            effective_max_projects(CUSTOMER), PLAN_LIMITS["starter"]["max_agency_projects"]
        )

    def test_agency_allowance_scales_with_plan(self):
        _make_agency()
        _make_sub(CUSTOMER, "pro")
        self.assertEqual(
            effective_max_projects(CUSTOMER), PLAN_LIMITS["pro"]["max_agency_projects"]
        )
        self.assertGreater(
            PLAN_LIMITS["pro"]["max_agency_projects"],
            PLAN_LIMITS["starter"]["max_agency_projects"],
        )

    def test_grandfathered_business_agency_stays_uncapped(self):
        """business declares max_agency_projects=0, which means uncapped."""
        _make_agency()
        _make_sub(CUSTOMER, "business")
        self.assertEqual(effective_max_projects(CUSTOMER), AGENCY_MAX_PROJECTS)


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
        _make_sub(CUSTOMER, "starter")
        allowance = PLAN_LIMITS["starter"]["max_agency_projects"]
        for i in range(allowance - 1):
            _make_org(CUSTOMER, f"brand{i}")
        self.assertFalse(project_limit_reached(CUSTOMER)[0])

        _make_org(CUSTOMER, "last")
        reached, msg = project_limit_reached(CUSTOMER)
        self.assertTrue(reached)
        self.assertIn(str(allowance), msg)


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
