"""
Database target check (Phase 1, step 1).

``check_db_target`` is the gate that runs before any migration: it states which
database a command is about to touch, refuses when that is not the expected
one, and never prints a credential.

Run: python manage.py test apps.growth_agent.tests.test_db_target --settings=config.settings.test
"""

from io import StringIO

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase

from ..services import db_target
from ..services.db_target import RoleFacts

PASSWORD = "s3cret-pass-do-not-print"
NEON_HOST = "ep-cool-river-123456-pooler.eu-west-2.aws.neon.tech"
SETTINGS = {
    "ENGINE": "django.db.backends.postgresql",
    "HOST": NEON_HOST,
    "PORT": "5432",
    "NAME": "signalor_staging",
    "USER": "signalor_app",
    "PASSWORD": PASSWORD,
    "OPTIONS": {"sslmode": "require", "password": PASSWORD},
}
SNAPSHOT = "br-snapshot-2026-11-11"


class DescribeTests(SimpleTestCase):
    def test_reports_where_the_connection_points(self):
        target = db_target.describe(SETTINGS, vendor="postgresql", current_role="signalor_app")
        self.assertEqual(target.vendor, "postgresql")
        self.assertEqual(target.host, NEON_HOST)
        self.assertEqual(target.port, "5432")
        self.assertEqual(target.name, "signalor_staging")
        self.assertEqual(target.role, "signalor_app")

    def test_extracts_the_neon_endpoint_id(self):
        target = db_target.describe(SETTINGS, vendor="postgresql", current_role="")
        self.assertEqual(target.neon_endpoint, "ep-cool-river-123456")

    def test_no_neon_endpoint_for_other_hosts(self):
        target = db_target.describe({**SETTINGS, "HOST": "db"}, vendor="postgresql", current_role="")
        self.assertEqual(target.neon_endpoint, "")

    def test_the_password_never_appears_in_the_description(self):
        target = db_target.describe(SETTINGS, vendor="postgresql", current_role="signalor_app")
        rendered = "\n".join(db_target.render(target)) + repr(target) + db_target.fingerprint(target)
        self.assertNotIn(PASSWORD, rendered)

    def test_fingerprint_is_stable_and_differs_between_databases(self):
        one = db_target.describe(SETTINGS, vendor="postgresql", current_role="")
        same = db_target.describe(dict(SETTINGS), vendor="postgresql", current_role="")
        other = db_target.describe(
            {**SETTINGS, "NAME": "signalor_prod"}, vendor="postgresql", current_role=""
        )
        self.assertEqual(db_target.fingerprint(one), db_target.fingerprint(same))
        self.assertNotEqual(db_target.fingerprint(one), db_target.fingerprint(other))


class TargetProblemsTests(SimpleTestCase):
    def setUp(self):
        self.target = db_target.describe(SETTINGS, vendor="postgresql", current_role="signalor_app")

    def problems(self, **overrides):
        expected = {"expect_host": NEON_HOST, "expect_db": "signalor_staging", "snapshot_ref": SNAPSHOT}
        return db_target.target_problems(self.target, **{**expected, **overrides})

    def test_matching_expectations_have_no_problems(self):
        self.assertEqual(self.problems(), [])

    def test_wrong_host_is_a_problem(self):
        self.assertEqual(len(self.problems(expect_host="some-other-host")), 1)

    def test_wrong_database_is_a_problem(self):
        self.assertEqual(len(self.problems(expect_db="signalor_prod")), 1)

    def test_missing_snapshot_reference_is_a_problem(self):
        for missing in ("", "   ", None):
            with self.subTest(snapshot_ref=missing):
                self.assertEqual(len(self.problems(snapshot_ref=missing)), 1)

    def test_missing_expectations_are_problems_not_wildcards(self):
        self.assertEqual(len(self.problems(expect_host=None, expect_db=None)), 2)

    def test_host_comparison_ignores_case_only(self):
        self.assertEqual(self.problems(expect_host=NEON_HOST.upper()), [])
        self.assertEqual(len(self.problems(expect_host=NEON_HOST + ".evil.test")), 1)


class RoleProblemsTests(SimpleTestCase):
    def facts(self, **overrides) -> RoleFacts:
        base = {"name": db_target.RUNTIME_ROLE, "is_superuser": False, "owned_tables": [], "member_of": []}
        return RoleFacts(**{**base, **overrides})

    def test_a_proper_runtime_role_passes(self):
        self.assertEqual(db_target.role_problems(db_target.ROLE_RUNTIME, self.facts()), [])

    def test_runtime_role_must_have_the_runtime_name(self):
        problems = db_target.role_problems(db_target.ROLE_RUNTIME, self.facts(name="postgres"))
        self.assertEqual(len(problems), 1)

    def test_runtime_role_must_not_be_a_superuser(self):
        problems = db_target.role_problems(db_target.ROLE_RUNTIME, self.facts(is_superuser=True))
        self.assertEqual(len(problems), 1)

    def test_runtime_role_must_not_own_a_monitored_table(self):
        facts = self.facts(owned_tables=["organizations_organization"])
        self.assertEqual(len(db_target.role_problems(db_target.ROLE_RUNTIME, facts)), 1)

    def test_runtime_role_must_not_be_able_to_become_the_owner(self):
        facts = self.facts(member_of=[db_target.OWNER_ROLE])
        self.assertEqual(len(db_target.role_problems(db_target.ROLE_RUNTIME, facts)), 1)

    def test_a_proper_owner_role_passes(self):
        facts = self.facts(name=db_target.OWNER_ROLE, member_of=[db_target.RUNTIME_ROLE])
        self.assertEqual(db_target.role_problems(db_target.ROLE_OWNER, facts), [])

    def test_owner_role_must_have_the_owner_name_and_not_be_a_superuser(self):
        self.assertEqual(len(db_target.role_problems(db_target.ROLE_OWNER, self.facts())), 1)
        facts = self.facts(name=db_target.OWNER_ROLE, is_superuser=True)
        self.assertEqual(len(db_target.role_problems(db_target.ROLE_OWNER, facts)), 1)

    def test_role_check_fails_closed_when_the_facts_cannot_be_read(self):
        for role in (db_target.ROLE_RUNTIME, db_target.ROLE_OWNER):
            with self.subTest(role=role):
                self.assertEqual(len(db_target.role_problems(role, None)), 1)


class CommandTests(TestCase):
    def setUp(self):
        self.host = connection.settings_dict.get("HOST") or ""
        self.name = str(connection.settings_dict.get("NAME") or "")

    def run_command(self, **options) -> str:
        defaults = {"expect_host": self.host, "expect_db": self.name, "snapshot_ref": SNAPSHOT}
        out = StringIO()
        call_command("check_db_target", stdout=out, **{**defaults, **options})
        return out.getvalue()

    def test_passes_when_the_target_is_the_expected_one(self):
        output = self.run_command()
        self.assertIn("OK", output)
        self.assertIn(self.name, output)
        self.assertIn(SNAPSHOT, output)

    def test_refuses_a_different_host(self):
        with self.assertRaises(CommandError):
            self.run_command(expect_host="definitely-not-this-host")

    def test_refuses_a_different_database(self):
        with self.assertRaises(CommandError):
            self.run_command(expect_db="definitely_not_this_db")

    def test_refuses_without_a_snapshot_reference(self):
        with self.assertRaises(CommandError):
            self.run_command(snapshot_ref="")

    def test_role_check_is_refused_when_the_database_cannot_answer_it(self):
        # The test database is sqlite, which has no roles. The check must fail
        # closed there instead of reporting a pass it could not verify.
        with self.assertRaises(CommandError):
            self.run_command(role=db_target.ROLE_RUNTIME)

    def test_changes_nothing(self):
        with self.assertNumQueries(0):
            self.run_command()
