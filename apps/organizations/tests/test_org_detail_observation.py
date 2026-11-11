"""SEC-1a-0: observe the brand edit/delete endpoint and lock the owner field.

The endpoint is still open during the observation, so these tests pin three
things: responses do not change (apart from owner_email being rejected), every
request is classified by whether the caller WOULD be authorized once sign-in is
enforced, and an alert fires for the attempts that would not be.

A valid token is not treated as authorization: a signed-in stranger deleting
someone else's brand must alert exactly like an anonymous caller does.

Run: manage.py test apps.organizations.tests.test_org_detail_observation
         --settings=config.settings.test
"""

import json
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import AccountProfile, AgencyMembership
from apps.organizations import observation
from apps.organizations.models import Organization
from apps.organizations.serializers import OrganizationSerializer
from core.auth.jwt import VerifiedUser

OWNER = "owner@example.test"
STRANGER = "stranger@example.test"
AGENCY = "agency@example.test"
AGENCY_ADMIN = "admin@example.test"
AGENCY_MEMBER = "member@example.test"
ALERT_INBOX = "alerts@example.test"
FAKE_TOKEN = "aaa.bbb.ccc"

OBSERVATION_EVENT = "org_detail_observation"
ALERT_EVENT = "org_detail_unauthorized_attempt"
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def as_verified(email: str):
    """Patch in the principal the JWT authenticator would have produced."""
    return mock.patch(
        "rest_framework.request.Request.user",
        new_callable=lambda: property(lambda _self: VerifiedUser(email=email)),
    )


def run_inline(target) -> None:
    target()


class ObservationTestCase(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", url="https://acme.test", owner_email=OWNER)
        AccountProfile.objects.create(email=AGENCY, account_type="agency")
        self.agency_org = Organization.objects.create(
            name="Agency brand", url="https://agency-brand.test", owner_email=AGENCY
        )
        AgencyMembership.objects.create(
            agency_email=AGENCY,
            member_email=AGENCY_ADMIN,
            role=AgencyMembership.Role.ADMIN,
            status=AgencyMembership.Status.ACTIVE,
        )
        AgencyMembership.objects.create(
            agency_email=AGENCY,
            member_email=AGENCY_MEMBER,
            role=AgencyMembership.Role.MEMBER,
            status=AgencyMembership.Status.ACTIVE,
        )

    def url(self, org: Organization | None = None, pk: int | None = None) -> str:
        return f"/api/organizations/{pk if pk is not None else (org or self.org).pk}/"

    def patch(self, body: dict, org: Organization | None = None, pk: int | None = None, **headers):
        return self.client.patch(
            self.url(org, pk), data=json.dumps(body), content_type="application/json", **headers
        )

    def delete(self, org: Organization | None = None, pk: int | None = None, **headers):
        return self.client.delete(self.url(org, pk), **headers)

    def captured(self, call):
        """Run ``call`` and return (response, log messages on the app logger)."""
        with self.assertLogs("apps", level="INFO") as logs:
            response = call()
        return response, [record.getMessage() for record in logs.records]

    def observation_line(self, messages: list[str]) -> str:
        lines = [m for m in messages if m.startswith(OBSERVATION_EVENT)]
        self.assertEqual(len(lines), 1, messages)
        return lines[0]

    def alert_lines(self, messages: list[str]) -> list[str]:
        return [m for m in messages if m.startswith(ALERT_EVENT)]


class WouldBeAuthorizedTests(ObservationTestCase):
    """One case per row of the authorization table SEC-1a will enforce."""

    def assert_verdict(self, method: str, email: str, org: Organization, expected: bool):
        self.assertIs(observation.would_be_authorized(method, email, org), expected)

    def test_owner_may_edit_and_delete(self):
        self.assert_verdict("PATCH", OWNER, self.org, True)
        self.assert_verdict("DELETE", OWNER, self.org, True)

    def test_owner_email_match_ignores_case_and_spaces(self):
        self.assert_verdict("DELETE", "  Owner@Example.Test ", self.org, True)

    def test_stranger_may_do_neither(self):
        self.assert_verdict("PATCH", STRANGER, self.org, False)
        self.assert_verdict("DELETE", STRANGER, self.org, False)

    def test_agency_admin_may_edit_but_not_delete(self):
        self.assert_verdict("PATCH", AGENCY_ADMIN, self.agency_org, True)
        self.assert_verdict("DELETE", AGENCY_ADMIN, self.agency_org, False)

    def test_agency_member_may_do_neither(self):
        self.assert_verdict("PATCH", AGENCY_MEMBER, self.agency_org, False)
        self.assert_verdict("DELETE", AGENCY_MEMBER, self.agency_org, False)

    def test_agency_owner_may_delete_its_own_brand(self):
        self.assert_verdict("DELETE", AGENCY, self.agency_org, True)

    def test_no_email_is_never_authorized(self):
        self.assert_verdict("PATCH", "", self.org, False)
        self.assert_verdict("DELETE", "", self.org, False)


class ObservationLineTests(ObservationTestCase):
    def test_anonymous_patch_is_observed_as_not_authorized(self):
        _, messages = self.captured(lambda: self.patch({"name": "Renamed"}))
        line = self.observation_line(messages)
        self.assertIn("method=PATCH", line)
        self.assertIn("caller=anonymous", line)
        self.assertIn("would_be_authorized=no", line)
        self.assertIn("owner_change_attempted=no", line)
        self.assertIn("status=200", line)
        self.assertIn(f"org_id={self.org.pk}", line)

    def test_unverified_token_is_observed_as_invalid_token(self):
        _, messages = self.captured(
            lambda: self.patch({"name": "Renamed"}, HTTP_AUTHORIZATION=f"Bearer {FAKE_TOKEN}")
        )
        line = self.observation_line(messages)
        self.assertIn("caller=invalid_token", line)
        self.assertIn("would_be_authorized=no", line)

    def test_malformed_authorization_header_is_invalid_token(self):
        _, messages = self.captured(lambda: self.delete(HTTP_AUTHORIZATION="garbage"))
        self.assertIn("caller=invalid_token", self.observation_line(messages))

    def test_verified_owner_is_observed_as_authorized(self):
        with as_verified(OWNER):
            _, messages = self.captured(lambda: self.patch({"name": "Renamed"}))
        line = self.observation_line(messages)
        self.assertIn("caller=verified", line)
        self.assertIn("would_be_authorized=yes", line)

    def test_verified_stranger_is_observed_as_not_authorized(self):
        with as_verified(STRANGER):
            _, messages = self.captured(lambda: self.patch({"name": "Renamed"}))
        line = self.observation_line(messages)
        self.assertIn("caller=verified", line)
        self.assertIn("would_be_authorized=no", line)

    def test_missing_brand_is_observed_as_not_applicable(self):
        response, messages = self.captured(lambda: self.delete(pk=999999))
        self.assertEqual(response.status_code, 404)
        line = self.observation_line(messages)
        self.assertIn("would_be_authorized=n/a", line)
        self.assertIn("status=404", line)

    def test_origin_host_is_recorded_without_scheme_or_path(self):
        _, messages = self.captured(
            lambda: self.patch({"name": "Renamed"}, HTTP_ORIGIN="https://App.Example.test:8443/x")
        )
        self.assertIn("origin_host=app.example.test ", self.observation_line(messages) + " ")

    def test_hostile_origin_cannot_inject_log_fields(self):
        _, messages = self.captured(
            lambda: self.patch({"name": "Renamed"}, HTTP_ORIGIN="https://evil status=200 caller=verified")
        )
        line = self.observation_line(messages)
        self.assertIn("origin_host=invalid", line)
        self.assertEqual(line.count("caller="), 1)
        self.assertEqual(line.count("status="), 1)

    def test_origin_with_control_characters_is_replaced(self):
        # A real server never delivers a newline inside a header, and the test
        # client refuses to send one, so this is checked on the function itself.
        for origin in (
            "https://evil.test\nstatus=200",
            "https://evil.test\r\nX: y",
            "not a url",
            "https://[bad",
        ):
            with self.subTest(origin=origin):
                request = mock.Mock(META={"HTTP_ORIGIN": origin})
                self.assertEqual(observation._origin_host(request), observation.INVALID_ORIGIN)

    def test_missing_origin_is_recorded_as_empty(self):
        self.assertEqual(observation._origin_host(mock.Mock(META={})), "")

    def test_other_organization_routes_are_not_observed(self):
        with self.assertNoLogs("apps", level="INFO"):
            self.client.get("/api/organizations/", {"email": OWNER})
            self.client.get("/api/organizations/check/", {"email": OWNER})


class BehaviorUnchangedTests(ObservationTestCase):
    """The endpoint stays open during the observation. Only owner_email is locked."""

    def test_anonymous_rename_still_works(self):
        response = self.patch({"name": "Renamed", "url": "https://renamed.test"})
        self.assertEqual(response.status_code, 200)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "Renamed")
        self.assertEqual(self.org.url, "https://renamed.test")
        self.assertEqual(set(response.json()), {"id", "name", "slug", "url", "owner_email", "created_at"})

    def test_anonymous_delete_still_works(self):
        response = self.delete()
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Organization.objects.filter(pk=self.org.pk).exists())

    def test_signed_in_stranger_delete_still_succeeds_during_observation(self):
        with as_verified(STRANGER):
            response = self.delete()
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Organization.objects.filter(pk=self.org.pk).exists())

    def test_missing_brand_keeps_its_404_body(self):
        response = self.patch({"name": "x"}, pk=999999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "Organization not found."})

    def test_invalid_body_is_still_rejected_and_observed(self):
        response, messages = self.captured(lambda: self.patch({"name": ""}))
        self.assertEqual(response.status_code, 400)
        self.assertIn("status=400", self.observation_line(messages))

    def test_malformed_json_edit_is_still_a_400_and_changes_nothing(self):
        def call():
            return self.client.patch(self.url(), data="{not json", content_type="application/json")

        response, messages = self.captured(call)
        self.assertEqual(response.status_code, 400)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "Acme")
        self.assertIn("status=400", self.observation_line(messages))

    def test_malformed_json_edit_of_a_missing_brand_is_still_a_404(self):
        response = self.client.patch(self.url(pk=999999), data="{not json", content_type="application/json")
        self.assertEqual(response.status_code, 404)

    def test_delete_never_reads_the_body(self):
        response = self.client.delete(self.url(), data="{not json", content_type="application/json")
        self.assertEqual(response.status_code, 204)

    def test_failing_authorization_check_never_changes_the_response(self):
        with mock.patch.object(observation, "would_be_authorized", side_effect=RuntimeError("boom")):
            with as_verified(OWNER):
                response, messages = self.captured(lambda: self.patch({"name": "Renamed"}))
        self.assertEqual(response.status_code, 200)
        self.assertIn("would_be_authorized=error", self.observation_line(messages))

    def test_failing_log_call_never_changes_the_response(self):
        with mock.patch.object(observation.logger, "info", side_effect=RuntimeError("boom")):
            response = self.patch({"name": "Renamed"})
        self.assertEqual(response.status_code, 200)


class OwnerEmailLockTests(ObservationTestCase):
    def assert_locked(self, response):
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "owner_email_read_only")
        self.org.refresh_from_db()
        self.assertEqual(self.org.owner_email, OWNER)

    def test_anonymous_owner_change_is_rejected(self):
        self.assert_locked(self.patch({"owner_email": STRANGER}))

    def test_verified_owner_cannot_change_owner_here_either(self):
        with as_verified(OWNER):
            self.assert_locked(self.patch({"owner_email": STRANGER}))

    def test_same_value_is_rejected_too(self):
        self.assert_locked(self.patch({"owner_email": OWNER}))

    def test_a_request_carrying_owner_email_changes_nothing_else(self):
        self.assert_locked(self.patch({"name": "Renamed", "owner_email": STRANGER}))
        self.assertEqual(self.org.name, "Acme")

    def test_owner_email_is_still_returned(self):
        self.assertEqual(self.patch({"name": "Renamed"}).json()["owner_email"], OWNER)

    def test_serializer_declares_owner_email_read_only(self):
        self.assertIn("owner_email", OrganizationSerializer.Meta.read_only_fields)
        self.assertIn("owner_email", OrganizationSerializer.Meta.fields)


class AlertTests(ObservationTestCase):
    """One case per row of the alert table."""

    def alerts_for(self, call) -> list[str]:
        _, messages = self.captured(call)
        return self.alert_lines(messages)

    def test_anonymous_delete_alerts(self):
        lines = self.alerts_for(self.delete)
        self.assertEqual(len(lines), 1)
        self.assertIn("attempt=delete", lines[0])
        self.assertIn("caller=anonymous", lines[0])

    def test_invalid_token_delete_alerts(self):
        lines = self.alerts_for(lambda: self.delete(HTTP_AUTHORIZATION=f"Bearer {FAKE_TOKEN}"))
        self.assertEqual(len(lines), 1)
        self.assertIn("caller=invalid_token", lines[0])

    def test_signed_in_stranger_delete_alerts(self):
        with as_verified(STRANGER):
            lines = self.alerts_for(self.delete)
        self.assertEqual(len(lines), 1)
        self.assertIn("attempt=delete", lines[0])
        self.assertIn("caller=verified", lines[0])

    def test_agency_admin_delete_alerts(self):
        with as_verified(AGENCY_ADMIN):
            lines = self.alerts_for(lambda: self.delete(org=self.agency_org))
        self.assertEqual(len(lines), 1)
        self.assertIn("attempt=delete", lines[0])

    def test_owner_change_attempt_alerts_for_anyone(self):
        self.assertIn(
            "attempt=owner_change", self.alerts_for(lambda: self.patch({"owner_email": STRANGER}))[0]
        )
        with as_verified(OWNER):
            lines = self.alerts_for(lambda: self.patch({"owner_email": STRANGER}))
        self.assertIn("attempt=owner_change", lines[0])

    def test_signed_in_stranger_rename_alerts(self):
        with as_verified(STRANGER):
            lines = self.alerts_for(lambda: self.patch({"name": "Renamed"}))
        self.assertEqual(len(lines), 1)
        self.assertIn("attempt=cross_account_edit", lines[0])

    def test_agency_member_rename_alerts(self):
        with as_verified(AGENCY_MEMBER):
            lines = self.alerts_for(lambda: self.patch({"name": "Renamed"}, org=self.agency_org))
        self.assertIn("attempt=cross_account_edit", lines[0])

    def test_owner_delete_does_not_alert(self):
        with as_verified(OWNER):
            self.assertEqual(self.alerts_for(self.delete), [])

    def test_owner_rename_does_not_alert(self):
        with as_verified(OWNER):
            self.assertEqual(self.alerts_for(lambda: self.patch({"name": "Renamed"})), [])

    def test_agency_admin_rename_does_not_alert(self):
        with as_verified(AGENCY_ADMIN):
            lines = self.alerts_for(lambda: self.patch({"name": "Renamed"}, org=self.agency_org))
        self.assertEqual(lines, [])

    def test_anonymous_rename_is_counted_but_does_not_alert(self):
        self.assertEqual(self.alerts_for(lambda: self.patch({"name": "Renamed"})), [])

    def test_delete_of_missing_brand_does_not_alert(self):
        self.assertEqual(self.alerts_for(lambda: self.delete(pk=999999)), [])

    def test_delete_alerts_when_the_authorization_check_fails(self):
        with mock.patch.object(observation, "would_be_authorized", side_effect=RuntimeError("boom")):
            with as_verified(OWNER):
                lines = self.alerts_for(self.delete)
        self.assertEqual(len(lines), 1)
        self.assertIn("would_be_authorized=error", lines[0])


@mock.patch.object(observation, "_run_in_background", run_inline)
class AlertEmailTests(ObservationTestCase):
    @override_settings(ORG_ENDPOINT_ALERT_EMAILS=[ALERT_INBOX])
    def test_alert_is_emailed_to_the_configured_inbox(self):
        self.delete()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [ALERT_INBOX])
        self.assertIn("delete", message.subject)
        self.assertIn(f"org_id={self.org.pk}", message.body)

    @override_settings(ORG_ENDPOINT_ALERT_EMAILS=[])
    def test_no_email_without_a_configured_inbox_but_the_alert_is_still_logged(self):
        _, messages = self.captured(self.delete)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(len(self.alert_lines(messages)), 1)

    @override_settings(ORG_ENDPOINT_ALERT_EMAILS=[ALERT_INBOX])
    def test_no_email_for_an_authorized_request(self):
        with as_verified(OWNER):
            self.delete()
        self.assertEqual(mail.outbox, [])

    @override_settings(ORG_ENDPOINT_ALERT_EMAILS=[ALERT_INBOX], CACHES=LOCMEM_CACHE)
    def test_repeat_alerts_for_the_same_brand_send_one_email_but_log_each_time(self):
        cache.clear()
        _, first = self.captured(lambda: self.patch({"owner_email": STRANGER}))
        _, second = self.captured(lambda: self.patch({"owner_email": STRANGER}))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(self.alert_lines(first)), 1)
        self.assertEqual(len(self.alert_lines(second)), 1)

    @override_settings(ORG_ENDPOINT_ALERT_EMAILS=[ALERT_INBOX], CACHES=LOCMEM_CACHE)
    def test_a_different_attempt_on_the_same_brand_is_emailed_separately(self):
        cache.clear()
        self.patch({"owner_email": STRANGER})
        self.delete()
        self.assertEqual(len(mail.outbox), 2)

    @override_settings(ORG_ENDPOINT_ALERT_EMAILS=[ALERT_INBOX])
    def test_a_failing_mail_backend_never_changes_the_response(self):
        with mock.patch.object(observation, "send_mail", side_effect=RuntimeError("smtp down")):
            response = self.delete()
        self.assertEqual(response.status_code, 204)


@mock.patch.object(observation, "_run_in_background", run_inline)
@override_settings(ORG_ENDPOINT_ALERT_EMAILS=[ALERT_INBOX])
class NothingSensitiveIsRecordedTests(ObservationTestCase):
    SECRET_NAME = "Very Secret Brand Name"

    def test_token_emails_and_body_never_reach_the_log_or_the_alert_email(self):
        with as_verified(STRANGER):
            _, messages = self.captured(
                lambda: self.patch(
                    {"name": self.SECRET_NAME, "owner_email": STRANGER},
                    HTTP_AUTHORIZATION=f"Bearer {FAKE_TOKEN}",
                )
            )
        recorded = "\n".join(messages + [m.subject + m.body for m in mail.outbox])
        self.assertTrue(mail.outbox)
        for secret in (FAKE_TOKEN, STRANGER, OWNER, self.SECRET_NAME, "acme.test"):
            self.assertNotIn(secret, recorded)
