"""Project (brand) cap behaviour in ``subscription_utils``.

Regression cover for the "Max (Internal) account cannot add a second brand" bug:
the individual single-brand invariant was applied before the internal-email
bypass, so an @optiminastic.com workspace was capped at one brand even though
``is_internal_email`` grants business-tier access.
"""

from django.test import TestCase

from apps.accounts.models import AGENCY_MAX_PROJECTS, AccountProfile
from apps.accounts.subscription_utils import effective_max_projects, project_limit_reached
from apps.organizations.models import Organization

INTERNAL = "devops@optiminastic.com"
CUSTOMER = "owner@acme-brand.com"


def _make_org(email: str, name: str) -> Organization:
    return Organization.objects.create(name=name, url=f"https://{name}.com", owner_email=email)


class EffectiveMaxProjectsTests(TestCase):
    def test_internal_email_is_not_capped_at_one(self):
        """The bug: internal accounts were folded into the individual cap."""
        self.assertEqual(effective_max_projects(INTERNAL), AGENCY_MAX_PROJECTS)

    def test_individual_customer_is_capped_at_one(self):
        self.assertEqual(effective_max_projects(CUSTOMER), 1)

    def test_agency_customer_gets_the_multi_brand_ceiling(self):
        AccountProfile.objects.create(
            email=CUSTOMER, account_type=AccountProfile.AccountType.AGENCY
        )
        self.assertGreaterEqual(effective_max_projects(CUSTOMER), AGENCY_MAX_PROJECTS)


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
