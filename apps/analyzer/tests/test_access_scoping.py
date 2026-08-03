"""Anonymous callers must not reach another account's runs, slugs or tasks.

Each test here reproduces an attack that worked against the previous code, all of
them verified against a live test database before the fix:

- ``GET /runs/?org_id=1`` returned another tenant's runs *including* ``slug``.
  ``org_id`` is Organization's sequential integer PK, so this walked every tenant
  from 1 upward, and ``slug`` is the capability the rest of the run API accepts.
- ``GET /runs/progress/?email=`` returned that account's ``slug`` outright.
- ``POST /actions/<id>/`` had no ownership check whatsoever: it fetched the row by
  integer id and read ``email`` off the row, so an anonymous request flipped a
  stranger's task to VERIFIED, wrote ``notes`` onto it and credited them 50 points.
- ``POST /start-analysis/`` created the run under a caller-supplied ``org_id``.

The suite runs with ``REQUIRE_VERIFIED_IDENTITY`` off, which is production's
current setting — these must hold in legacy mode, not only once the frontend
ships JWTs. What legacy mode still permits is someone who *knows* an address
claiming it; that closes when the flag flips, and every endpoint here now resolves
identity through ``core.auth.identity`` so the flag is the only change needed.
"""

from django.test import TestCase, override_settings

from apps.analyzer.models import AnalysisRun, UserAction, UserGamification
from apps.organizations.models import Organization


@override_settings(REQUIRE_VERIFIED_IDENTITY=False)
class RunListScopingTests(TestCase):
    def setUp(self):
        self.victim_org = Organization.objects.create(
            name="VictimCorp", owner_email="ceo@victimcorp.com", url="https://victimcorp.com"
        )
        self.victim_run = AnalysisRun.objects.create(
            url="https://victimcorp.com/secret-launch",
            organization=self.victim_org,
            email="ceo@victimcorp.com",
        )

    def test_guessed_org_id_no_longer_returns_another_tenants_runs(self):
        resp = self.client.get(
            "/api/analyzer/runs/",
            {"org_id": self.victim_org.pk, "email": "attacker@evil.com"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_org_id_without_any_identity_is_rejected(self):
        """The original bug: org_id alone, no email, full tenant dump."""
        resp = self.client.get("/api/analyzer/runs/", {"org_id": self.victim_org.pk})
        self.assertEqual(resp.status_code, 400)

    def test_the_owner_still_gets_their_own_runs(self):
        resp = self.client.get(
            "/api/analyzer/runs/",
            {"org_id": self.victim_org.pk, "email": "ceo@victimcorp.com"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)

    def test_email_scoped_listing_still_works(self):
        resp = self.client.get("/api/analyzer/runs/", {"email": "ceo@victimcorp.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()[0]["slug"], self.victim_run.slug)


@override_settings(REQUIRE_VERIFIED_IDENTITY=True)
class EnforcedIdentityTests(TestCase):
    """With the flag on, a claimed email buys nothing — the residual hole closes."""

    def setUp(self):
        self.org = Organization.objects.create(
            name="VictimCorp", owner_email="ceo@victimcorp.com", url="https://victimcorp.com"
        )
        AnalysisRun.objects.create(
            url="https://victimcorp.com/x", organization=self.org, email="ceo@victimcorp.com"
        )

    def test_claiming_the_victims_email_is_rejected(self):
        resp = self.client.get("/api/analyzer/runs/", {"email": "ceo@victimcorp.com"})
        self.assertEqual(resp.status_code, 401)

    def test_progress_endpoint_requires_a_verified_caller(self):
        resp = self.client.get("/api/analyzer/runs/progress/", {"email": "ceo@victimcorp.com"})
        self.assertEqual(resp.status_code, 401)


@override_settings(REQUIRE_VERIFIED_IDENTITY=False)
class ActionOwnershipTests(TestCase):
    def setUp(self):
        self.victim = UserAction.objects.create(
            user_email="victim@corp.com",
            action_type="add_faq",
            title="Victim's task",
            points_value=50,
            score_before=10,
            status=UserAction.ActionStatus.PENDING,
        )

    def _update(self, **body):
        return self.client.post(
            f"/api/analyzer/actions/{self.victim.pk}/", body, content_type="application/json"
        )

    def test_anonymous_update_is_rejected(self):
        self.assertEqual(self._update(status="verified").status_code, 400)

    def test_another_account_cannot_verify_or_award_points(self):
        resp = self._update(email="attacker@evil.com", status="verified", score_after=99, notes="injected")
        self.assertEqual(resp.status_code, 404)

        self.victim.refresh_from_db()
        self.assertEqual(self.victim.status, UserAction.ActionStatus.PENDING)
        self.assertNotEqual(self.victim.notes, "injected")
        self.assertFalse(UserGamification.objects.filter(user_email="victim@corp.com").exists())

    def test_the_owner_can_still_update_their_own_task(self):
        resp = self._update(email="victim@corp.com", status="in_progress")
        self.assertEqual(resp.status_code, 200)
        self.victim.refresh_from_db()
        self.assertEqual(self.victim.status, UserAction.ActionStatus.IN_PROGRESS)

    def test_verify_endpoint_rejects_a_stranger(self):
        """Also an amplification vector: it re-crawls a live site per call."""
        resp = self.client.post(
            f"/api/analyzer/actions/{self.victim.pk}/verify/",
            {"email": "attacker@evil.com"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_an_assignee_can_act_on_work_assigned_to_them(self):
        self.victim.assignee_email = "teammate@corp.com"
        self.victim.save(update_fields=["assignee_email"])
        resp = self._update(email="teammate@corp.com", status="in_progress")
        self.assertEqual(resp.status_code, 200)


@override_settings(REQUIRE_VERIFIED_IDENTITY=False)
class ScoreHistoryScopingTests(TestCase):
    """Every row of this response carries ``slug``, same as the run list."""

    def setUp(self):
        self.victim_org = Organization.objects.create(
            name="VictimCorp", owner_email="ceo@victimcorp.com", url="https://victimcorp.com"
        )
        AnalysisRun.objects.create(
            url="https://victimcorp.com/x",
            organization=self.victim_org,
            email="ceo@victimcorp.com",
            status="complete",
            composite_score=71.0,
        )

    def test_a_stranger_cannot_read_another_brands_history(self):
        resp = self.client.get(
            "/api/analyzer/runs/history/",
            {"org_id": self.victim_org.pk, "email": "attacker@evil.com"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_the_owner_still_gets_their_history(self):
        resp = self.client.get(
            "/api/analyzer/runs/history/",
            {"org_id": self.victim_org.pk, "email": "ceo@victimcorp.com"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)


@override_settings(REQUIRE_VERIFIED_IDENTITY=False)
class StartAnalysisOrgScopingTests(TestCase):
    def setUp(self):
        self.victim_org = Organization.objects.create(
            name="VictimCorp", owner_email="ceo@victimcorp.com", url="https://victimcorp.com"
        )

    def test_a_run_cannot_be_created_under_someone_elses_org(self):
        resp = self.client.post(
            "/api/analyzer/start-analysis/",
            {
                "url": "https://evil.com",
                "email": "attacker@evil.com",
                "org_id": self.victim_org.pk,
                "run_type": "single_page",
            },
            content_type="application/json",
        )
        self.assertIn(resp.status_code, (401, 404))
        self.assertFalse(AnalysisRun.objects.filter(organization=self.victim_org).exists())
