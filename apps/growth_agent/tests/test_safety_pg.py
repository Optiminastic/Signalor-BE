"""
Safety-change triggers and confirmation concurrency, on real Postgres.

Run: TEST_DATABASE_URL=... python manage.py test apps.growth_agent.tests.test_safety_pg \
        --settings=config.settings.test_postgres
"""

from unittest import mock, skipUnless

from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase, override_settings

from apps.github_agent.models import GithubInstallation
from apps.integrations.models import Integration
from apps.organizations.models import Organization

from ..models import GrowthProgram, GrowthSafetyChange, SafetyConfirmationEvent
from ..services.control import confirmations, safety
from .test_concurrency_pg import run_together

OWNER = "owner@safety.example"
KEY = "k" * 40


@skipUnless(connection.vendor == "postgresql", "needs Postgres triggers")
@override_settings(GROWTH_CONFIRMATION_HMAC_KEY=KEY)
class SafetyTriggerTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Safe", url="https://safe.example", owner_email=OWNER)
        self.program = GrowthProgram.objects.create(organization=self.org, created_by_email=OWNER)
        self.wp = Integration.objects.create(
            organization=self.org,
            provider="wordpress",
            is_active=True,
            metadata={"site_url": "https://a.example"},
        )

    def changes(self, **filters):
        return GrowthSafetyChange.objects.filter(organization=self.org, **filters)

    def test_an_unattributed_site_change_is_recorded_and_blocks(self):
        Integration.objects.filter(pk=self.wp.pk).update(metadata={"site_url": "https://b.example"})
        change = self.changes(field="metadata.site_url").get()
        self.assertEqual(
            (change.actor, change.old_display, change.new_display),
            ("unknown", "https://a.example", "https://b.example"),
        )
        self.assertEqual(change.new_digest, safety.digest("https://b.example"))
        self.assertTrue(safety.blocked(self.org.pk))

    def test_an_attributed_change_records_its_actor_and_does_not_block(self):
        with transaction.atomic():
            safety.set_actor(safety.owner_actor(OWNER))
            Integration.objects.filter(pk=self.wp.pk).update(metadata={"site_url": "https://b.example"})
        self.assertTrue(self.changes(field="metadata.site_url").get().actor.startswith("owner:"))
        self.assertFalse(safety.blocked(self.org.pk))

    def test_the_actor_does_not_leak_past_its_transaction(self):
        with transaction.atomic():
            safety.set_actor("system:test")
        Integration.objects.filter(pk=self.wp.pk).update(metadata={"site_url": "https://c.example"})
        self.assertEqual(self.changes(field="metadata.site_url").get().actor, "unknown")

    def test_owner_email_change_is_masked_and_blocks(self):
        Organization.objects.filter(pk=self.org.pk).update(owner_email="attacker@evil.example")
        change = self.changes(field="owner_email").get()
        self.assertEqual(change.new_display, "a***@evil.example")
        self.assertTrue(safety.ownership_in_doubt(self.org.pk))

    def test_a_reversed_change_still_blocks(self):
        Organization.objects.filter(pk=self.org.pk).update(owner_email="x@evil.example")
        Organization.objects.filter(pk=self.org.pk).update(owner_email=OWNER)
        self.assertEqual(self.changes(field="owner_email").count(), 2)
        self.assertTrue(safety.blocked(self.org.pk))

    def test_gmail_mailbox_and_github_repo_changes(self):
        gmail = Integration.objects.create(
            organization=self.org, provider="gmail", metadata={"email": "a@b.example"}
        )
        Integration.objects.filter(pk=gmail.pk).update(metadata={"email": "c@d.example"})
        self.assertEqual(self.changes(field="metadata.email").get().new_display, "c***@d.example")
        install = GithubInstallation.objects.create(
            organization=self.org, installation_id=1, repo_full_name="a/b"
        )
        GithubInstallation.objects.filter(pk=install.pk).update(repo_full_name="evil/repo")
        self.assertEqual(self.changes(field="repo_full_name").get().new_display, "evil/repo")

    def test_deactivation_is_recorded_but_does_not_block(self):
        Integration.objects.filter(pk=self.wp.pk).update(is_active=False)
        self.assertTrue(self.changes(field="is_active").exists())
        self.assertFalse(safety.blocked(self.org.pk))

    def test_change_records_cannot_be_altered(self):
        Integration.objects.filter(pk=self.wp.pk).update(metadata={"site_url": "https://b.example"})
        for sql in (
            "UPDATE growth_agent_growthsafetychange SET actor = 'admin:1'",
            "DELETE FROM growth_agent_growthsafetychange",
        ):
            with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(sql)

    def test_deleting_the_brand_erases_its_safety_records(self):
        Integration.objects.filter(pk=self.wp.pk).update(metadata={"site_url": "https://b.example"})
        org_id = self.org.pk
        self.org.delete()
        self.assertFalse(GrowthSafetyChange.objects.filter(organization_id=org_id).exists())

    def test_concurrent_wrong_codes_never_exceed_five_attempts(self):
        Integration.objects.filter(pk=self.wp.pk).update(metadata={"site_url": "https://b.example"})
        change = self.changes(field="metadata.site_url").get()
        with mock.patch.object(confirmations, "_email"):
            confirmations.issue(self.program, change.id)
        compared = []
        real = confirmations._verifier

        def counting(*args):
            compared.append(1)
            return real(*args)

        request = confirmations.ConfirmRequest(
            "00000000", "https://a.example", "https://b.example", "test", OWNER
        )

        def attempt():
            program = GrowthProgram.objects.select_related("organization").get(pk=self.program.pk)
            return confirmations.confirm(program, change.id, request)

        with mock.patch.object(confirmations, "_verifier", side_effect=counting):
            run_together(*[attempt] * 20)
        self.assertEqual(SafetyConfirmationEvent.objects.filter(event="attempt").count(), 5)
        self.assertLessEqual(len(compared), 5)
        self.assertFalse(SafetyConfirmationEvent.objects.filter(event="verified").exists())
