"""Regression: the integrations status poll must never create an Organization.

Root cause of the "Individual accounts include a single brand" onboarding bug:
`IntegrationStatusView` (a high-frequency dashboard/sidebar poll) resolved the org
via a helper that auto-created a default org named from the email prefix. That
premature org used up the plan's single-brand slot, so onboarding's URL step then
failed. Orgs must be created only during onboarding.
"""

from django.test import TestCase
from django.urls import reverse

from apps.organizations.models import Organization

_NEW_EMAIL = "tech1@optiminastic.com"


class StatusDoesNotCreateOrgTests(TestCase):
    def test_status_poll_does_not_create_org_and_returns_empty(self):
        url = reverse("integrations:status")
        self.assertEqual(Organization.objects.filter(owner_email=_NEW_EMAIL).count(), 0)

        resp = self.client.get(url, {"email": _NEW_EMAIL})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])
        # The poll must NOT have created a brand for this user.
        self.assertEqual(
            Organization.objects.filter(owner_email=_NEW_EMAIL).count(),
            0,
            "status poll auto-created an org — this re-breaks onboarding",
        )

    def test_status_returns_integrations_for_existing_org(self):
        org = Organization.objects.create(name="Tech1", url="https://x.co", owner_email=_NEW_EMAIL)
        resp = self.client.get(reverse("integrations:status"), {"email": _NEW_EMAIL})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])  # org exists but no integrations connected yet
        # still exactly one org — the read created nothing extra
        self.assertEqual(Organization.objects.filter(owner_email=_NEW_EMAIL).count(), 1)
        self.assertEqual(Organization.objects.get(pk=org.pk).name, "Tech1")
