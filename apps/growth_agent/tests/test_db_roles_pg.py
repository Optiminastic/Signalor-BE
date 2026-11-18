"""
The database role split, on real Postgres: as the runtime role (signalor_app)
the Phase 1 protections can't be switched off or bypassed.

The test creates the two roles on the local test cluster (needs a superuser
connection, so it skips otherwise), gives them throwaway passwords, applies the
split to the test database, then connects as signalor_app.

Run: TEST_DATABASE_URL=... python manage.py test apps.growth_agent.tests.test_db_roles_pg \
        --settings=config.settings.test_postgres
"""

import secrets
from pathlib import Path
from unittest import skipUnless

import psycopg2
from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase

from apps.integrations.models import Integration
from apps.organizations.models import Organization

from ..db import role_split
from ..models import GrowthSafetyChange

ROLES_SQL = Path(settings.BASE_DIR) / "deploy" / "sql" / "phase1_roles.sql"
APP, OWNER = "signalor_app", "signalor_owner"
REFUSED = ("42501",)  # insufficient_privilege


def _superuser() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        return bool(cursor.fetchone()[0])


@skipUnless(connection.vendor == "postgresql", "needs Postgres roles")
class RuntimeRoleTests(TransactionTestCase):
    def setUp(self):
        if not _superuser():
            self.skipTest("creating roles needs a superuser connection")
        self.password = secrets.token_urlsafe(24)
        with connection.cursor() as cursor:
            cursor.execute(ROLES_SQL.read_text(encoding="utf-8"))
            cursor.execute(f"ALTER ROLE {APP} PASSWORD %s", [self.password])
            cursor.execute(f"GRANT CONNECT ON DATABASE {connection.settings_dict['NAME']} TO {APP}")
            role_split.apply(cursor)
        self.org = Organization.objects.create(name="R", url="https://r.example", owner_email="o@r.example")
        self.wp = Integration.objects.create(
            organization=self.org, provider="wordpress", metadata={"site_url": "https://a"}
        )
        self.app = psycopg2.connect(
            host=connection.settings_dict["HOST"] or "localhost",
            port=connection.settings_dict["PORT"] or 5432,
            dbname=connection.settings_dict["NAME"],
            user=APP,
            password=self.password,
        )
        self.addCleanup(self.app.close)

    def as_app(self, sql, params=None):
        with self.app.cursor() as cursor:
            try:
                cursor.execute(sql, params)
                self.app.commit()
                return None
            except psycopg2.Error as exc:
                self.app.rollback()
                return exc.pgcode

    def test_the_split_leaves_nothing_to_fix(self):
        with connection.cursor() as cursor:
            self.assertEqual(role_split.problems(cursor), [])

    def test_every_change_writing_function_runs_as_the_owner(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT proname, pg_get_userbyid(proowner), prosecdef FROM pg_proc "
                "WHERE proname IN ('growth_safety_insert', 'growth_watch_organization', "
                "'growth_watch_integration', 'growth_watch_github') ORDER BY 1"
            )
            rows = cursor.fetchall()
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(owner == OWNER and definer for _, owner, definer in rows), rows)

    def test_triggers_cannot_be_switched_off_or_dropped(self):
        for table in (
            "organizations_organization",
            "integrations_integration",
            "growth_agent_approvaldecision",
        ):
            with self.subTest(table=table):
                self.assertIn(self.as_app(f"ALTER TABLE {table} DISABLE TRIGGER ALL"), REFUSED)
                self.assertIn(self.as_app(f"ALTER TABLE {table} OWNER TO {APP}"), REFUSED)
        self.assertIn(
            self.as_app("DROP TRIGGER growth_append_only ON growth_agent_approvaldecision"), REFUSED
        )
        self.assertIn(
            self.as_app(
                "CREATE OR REPLACE FUNCTION growth_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN OLD; END $$"
            ),
            REFUSED,
        )
        self.assertIn(self.as_app("SET session_replication_role = replica"), REFUSED)
        self.assertIn(self.as_app(f"SET ROLE {OWNER}"), REFUSED)

    def test_append_only_tables_cannot_be_changed_or_truncated(self):
        self.assertIn(self.as_app("UPDATE growth_agent_approvaldecision SET reason = 'x'"), REFUSED)
        self.assertIn(self.as_app("UPDATE growth_agent_agentactionlog SET detail = '{}'::jsonb"), REFUSED)
        self.assertIn(self.as_app("TRUNCATE growth_agent_agentactionlog"), REFUSED)
        self.assertIn(self.as_app("TRUNCATE organizations_organization CASCADE"), REFUSED)

    def test_change_records_cannot_be_forged_but_are_still_written(self):
        forged = (
            "INSERT INTO growth_agent_growthsafetychange (organization_id, entity, entity_id, provider, field, "
            "old_digest, new_digest, old_display, new_display, actor, created_at) "
            f"VALUES ({self.org.pk}, 'x', 1, '', 'f', '', '', '', '', 'admin:1', now())"
        )
        self.assertIn(self.as_app(forged), REFUSED)
        call = f"SELECT growth_safety_insert({self.org.pk}, 'x', 1, '', 'f', 'a', 'b', false)"
        self.assertIn(self.as_app(call), REFUSED)
        # An ordinary update by the runtime role still records its change, through the trigger.
        self.assertIsNone(
            self.as_app(
                'UPDATE integrations_integration SET metadata = \'{"site_url": "https://b"}\' WHERE id = %s',
                [self.wp.pk],
            )
        )
        self.assertTrue(
            GrowthSafetyChange.objects.filter(organization=self.org, field="metadata.site_url").exists()
        )

    def test_ordinary_work_still_works(self):
        self.assertIsNone(
            self.as_app("UPDATE organizations_organization SET name = 'New' WHERE id = %s", [self.org.pk])
        )
        self.assertIsNone(self.as_app("SELECT count(*) FROM growth_agent_growthprogram"))
