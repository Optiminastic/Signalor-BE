"""SEC-1a: brand edit and delete require a verified caller who owns the brand.

Rules pinned here:
- No verified identity: 401, whatever the brand id. A legacy ``?email=`` or
  body ``email`` is never accepted as identity.
- Edit: the owner, or an admin of the agency that owns the brand.
- Delete: the owner only.
- Out of scope and nonexistent look identical (404, same body), so the
  sequential brand id cannot be probed.
- Only ``name`` and ``url`` can be edited. Every other key is rejected.

Run: manage.py test apps.organizations.tests.test_org_detail_authz
         --settings=config.settings.test
"""

import json
from unittest import mock

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase, override_settings

from apps.accounts.models import AccountProfile, AgencyMembership, User
from apps.organizations import access
from apps.organizations.admin import OrganizationAdmin
from apps.organizations.models import Organization
from apps.organizations.serializers import OrganizationSerializer, OrganizationUpdateSerializer
from core.auth.jwt import VerifiedUser

OWNER = "owner@example.test"
STRANGER = "stranger@example.test"
AGENCY = "agency@example.test"
AGENCY_ADMIN = "admin@example.test"
AGENCY_MEMBER = "member@example.test"
JWKS_URL = "https://auth.example.test/jwks"
MISSING_PK = 999999
NOT_FOUND_BODY = {"error": "Organization not found."}
EDITABLE_FIELDS = {"name", "url"}


def as_verified(email: str):
    """Patch in the principal the JWT authenticator would have produced."""
    return mock.patch(
        "rest_framework.request.Request.user",
        new_callable=lambda: property(lambda _self: VerifiedUser(email=email)),
    )


@override_settings(BETTER_AUTH_JWKS_URL=JWKS_URL)
class AuthzTestCase(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", url="https://acme.test", owner_email=OWNER)
        self.other_org = Organization.objects.create(
            name="Other", url="https://other.test", owner_email=STRANGER
        )
        AccountProfile.objects.create(email=AGENCY, account_type="agency")
        self.agency_org = Organization.objects.create(
            name="Agency brand", url="https://agency-brand.test", owner_email=AGENCY
        )
        for member, role in (
            (AGENCY_ADMIN, AgencyMembership.Role.ADMIN),
            (AGENCY_MEMBER, AgencyMembership.Role.MEMBER),
        ):
            AgencyMembership.objects.create(
                agency_email=AGENCY,
                member_email=member,
                role=role,
                status=AgencyMembership.Status.ACTIVE,
            )

    def url(self, org: Organization | None = None, pk: int | None = None) -> str:
        return f"/api/organizations/{pk if pk is not None else (org or self.org).pk}/"

    def patch(self, body, org=None, pk=None, query: str = "", **headers):
        return self.client.patch(
            self.url(org, pk) + query, data=json.dumps(body), content_type="application/json", **headers
        )

    def delete(self, org=None, pk=None, query: str = "", **headers):
        return self.client.delete(self.url(org, pk) + query, **headers)

    def assert_unchanged(self, org: Organization, name: str):
        org.refresh_from_db()
        self.assertEqual(org.name, name)


class AccessRuleTests(AuthzTestCase):
    def test_owner_may_edit_and_delete(self):
        self.assertTrue(access.may_edit(OWNER, self.org))
        self.assertTrue(access.may_delete(OWNER, self.org))

    def test_stranger_may_do_neither(self):
        self.assertFalse(access.may_edit(STRANGER, self.org))
        self.assertFalse(access.may_delete(STRANGER, self.org))

    def test_agency_admin_may_edit_but_not_delete(self):
        self.assertTrue(access.may_edit(AGENCY_ADMIN, self.agency_org))
        self.assertFalse(access.may_delete(AGENCY_ADMIN, self.agency_org))

    def test_agency_member_may_do_neither(self):
        self.assertFalse(access.may_edit(AGENCY_MEMBER, self.agency_org))
        self.assertFalse(access.may_delete(AGENCY_MEMBER, self.agency_org))

    def test_blank_identity_is_never_authorized(self):
        for email in ("", "   ", None):
            with self.subTest(email=email):
                self.assertFalse(access.may_edit(email, self.org))
                self.assertFalse(access.may_delete(email, self.org))

    def test_brand_with_a_blank_owner_is_not_matched_by_a_blank_identity(self):
        self.org.owner_email = ""
        self.assertFalse(access.may_delete("", self.org))
        self.assertFalse(access.may_edit("", self.org))


class UnauthenticatedTests(AuthzTestCase):
    def test_anonymous_edit_is_401_and_changes_nothing(self):
        response = self.patch({"name": "Hacked"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "identity_unverified")
        self.assert_unchanged(self.org, "Acme")

    def test_anonymous_delete_is_401_and_deletes_nothing(self):
        self.assertEqual(self.delete().status_code, 401)
        self.assertTrue(Organization.objects.filter(pk=self.org.pk).exists())

    def test_legacy_email_in_query_or_body_is_not_identity(self):
        self.assertEqual(self.patch({"name": "Hacked"}, query=f"?email={OWNER}").status_code, 401)
        self.assertEqual(self.patch({"name": "Hacked", "email": OWNER}).status_code, 401)
        self.assertEqual(self.delete(query=f"?email={OWNER}").status_code, 401)
        self.assert_unchanged(self.org, "Acme")
        self.assertTrue(Organization.objects.filter(pk=self.org.pk).exists())

    def test_unverified_bearer_token_is_401(self):
        response = self.delete(HTTP_AUTHORIZATION="Bearer aaa.bbb.ccc")
        self.assertEqual(response.status_code, 401)
        self.assertTrue(Organization.objects.filter(pk=self.org.pk).exists())

    def test_anonymous_caller_cannot_tell_a_real_brand_from_a_missing_one(self):
        real = self.delete()
        missing = self.delete(pk=MISSING_PK)
        self.assertEqual((real.status_code, real.json()), (missing.status_code, missing.json()))

    @override_settings(REQUIRE_VERIFIED_IDENTITY=False)
    def test_enforced_even_when_the_global_flag_is_off(self):
        self.assertEqual(self.patch({"name": "Hacked"}).status_code, 401)

    @override_settings(BETTER_AUTH_JWKS_URL="")
    def test_no_jwks_configured_is_503_and_changes_nothing(self):
        for response in (self.patch({"name": "Hacked"}), self.delete()):
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["code"], "auth_unavailable")
        self.assert_unchanged(self.org, "Acme")
        self.assertTrue(Organization.objects.filter(pk=self.org.pk).exists())


class OwnerTests(AuthzTestCase):
    def test_owner_edits_name_and_url(self):
        with as_verified(OWNER):
            response = self.patch({"name": "Renamed", "url": "https://renamed.test"})
        self.assertEqual(response.status_code, 200)
        self.org.refresh_from_db()
        self.assertEqual((self.org.name, self.org.url), ("Renamed", "https://renamed.test"))
        self.assertEqual(self.org.normalized_url, "renamed.test")
        self.assertEqual(self.org.owner_email, OWNER)

    def test_response_shape_is_unchanged(self):
        with as_verified(OWNER):
            body = self.patch({"name": "Renamed"}).json()
        self.assertEqual(set(body), set(OrganizationSerializer.Meta.fields))
        self.assertEqual(body["owner_email"], OWNER)

    def test_owner_deletes_own_brand_only(self):
        with as_verified(OWNER):
            self.assertEqual(self.delete().status_code, 204)
        self.assertFalse(Organization.objects.filter(pk=self.org.pk).exists())
        self.assertTrue(Organization.objects.filter(pk=self.other_org.pk).exists())
        self.assertTrue(Organization.objects.filter(pk=self.agency_org.pk).exists())

    def test_invalid_value_is_still_a_400(self):
        with as_verified(OWNER):
            self.assertEqual(self.patch({"name": ""}).status_code, 400)
            self.assertEqual(self.patch({"url": "not a url"}).status_code, 400)
        self.assert_unchanged(self.org, "Acme")

    def test_malformed_json_is_a_400(self):
        with as_verified(OWNER):
            response = self.client.patch(self.url(), data="{not json", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assert_unchanged(self.org, "Acme")


class OutOfScopeTests(AuthzTestCase):
    def test_stranger_edit_is_404_and_changes_nothing(self):
        with as_verified(STRANGER):
            response = self.patch({"name": "Hacked"})
        self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND_BODY))
        self.assert_unchanged(self.org, "Acme")

    def test_stranger_delete_is_404_and_deletes_nothing(self):
        with as_verified(STRANGER):
            response = self.delete()
        self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND_BODY))
        self.assertTrue(Organization.objects.filter(pk=self.org.pk).exists())

    def test_out_of_scope_and_missing_are_indistinguishable(self):
        with as_verified(STRANGER):
            for call in (self.delete, lambda **kw: self.patch({"name": "x"}, **kw)):
                real, missing = call(), call(pk=MISSING_PK)
                self.assertEqual((real.status_code, real.json()), (missing.status_code, missing.json()))

    def test_walking_the_ids_reveals_nothing(self):
        last_pk = Organization.objects.order_by("-pk").first().pk
        with as_verified(AGENCY_MEMBER):
            statuses = {self.delete(pk=pk).status_code for pk in range(1, last_pk + 3)}
        self.assertEqual(statuses, {404})
        self.assertEqual(Organization.objects.count(), 3)

    def test_stranger_cannot_take_a_brand_by_sending_owner_email(self):
        with as_verified(STRANGER):
            response = self.patch({"owner_email": STRANGER})
        self.assertEqual(response.status_code, 404)
        self.org.refresh_from_db()
        self.assertEqual(self.org.owner_email, OWNER)


class AgencyTests(AuthzTestCase):
    def test_agency_admin_may_edit(self):
        with as_verified(AGENCY_ADMIN):
            self.assertEqual(self.patch({"name": "Renamed"}, org=self.agency_org).status_code, 200)
        self.assert_unchanged(self.agency_org, "Renamed")

    def test_agency_admin_may_not_delete(self):
        with as_verified(AGENCY_ADMIN):
            response = self.delete(org=self.agency_org)
        self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND_BODY))
        self.assertTrue(Organization.objects.filter(pk=self.agency_org.pk).exists())

    def test_agency_owner_may_delete(self):
        with as_verified(AGENCY):
            self.assertEqual(self.delete(org=self.agency_org).status_code, 204)

    def test_agency_member_may_neither_edit_nor_delete(self):
        with as_verified(AGENCY_MEMBER):
            self.assertEqual(self.patch({"name": "Hacked"}, org=self.agency_org).status_code, 404)
            self.assertEqual(self.delete(org=self.agency_org).status_code, 404)
        self.assert_unchanged(self.agency_org, "Agency brand")

    def test_agency_admin_cannot_reach_brands_outside_the_agency(self):
        with as_verified(AGENCY_ADMIN):
            self.assertEqual(self.patch({"name": "Hacked"}).status_code, 404)
        self.assert_unchanged(self.org, "Acme")


class FieldAllowlistTests(AuthzTestCase):
    def test_update_serializer_accepts_exactly_name_and_url(self):
        self.assertEqual(set(OrganizationUpdateSerializer.Meta.fields), EDITABLE_FIELDS)

    def test_every_other_model_field_is_rejected(self):
        """Reads the model, so a field added later is covered without editing this test."""
        other_fields = [
            field.name
            for field in Organization._meta.get_fields()
            if getattr(field, "concrete", False) and field.name not in EDITABLE_FIELDS
        ]
        self.assertIn("owner_email", other_fields)
        self.assertIn("platform", other_fields)
        before = Organization.objects.filter(pk=self.org.pk).values().get()
        with as_verified(OWNER):
            for name in other_fields:
                with self.subTest(field=name):
                    response = self.patch({name: "tampered"})
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(Organization.objects.filter(pk=self.org.pk).values().get(), before)

    def test_unknown_key_rejects_the_whole_request(self):
        with as_verified(OWNER):
            response = self.patch({"name": "Renamed", "is_admin": True})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "field_not_editable")
        self.assertEqual(response.json()["fields"], ["is_admin"])
        self.assert_unchanged(self.org, "Acme")

    def test_owner_email_is_rejected_whatever_its_value(self):
        with as_verified(OWNER):
            for value in (STRANGER, OWNER):
                with self.subTest(value=value):
                    response = self.patch({"name": "Renamed", "owner_email": value})
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()["code"], "owner_email_read_only")
        self.org.refresh_from_db()
        self.assertEqual((self.org.name, self.org.owner_email), ("Acme", OWNER))

    def test_agency_admin_has_the_same_allowlist(self):
        with as_verified(AGENCY_ADMIN):
            for body in ({"platform": "shopify"}, {"owner_email": AGENCY_ADMIN}, {"slug": "x"}):
                with self.subTest(body=body):
                    self.assertEqual(self.patch(body, org=self.agency_org).status_code, 400)
        self.agency_org.refresh_from_db()
        self.assertEqual(self.agency_org.owner_email, AGENCY)

    def test_a_field_made_writable_on_the_read_serializer_stays_rejected(self):
        with mock.patch.object(OrganizationSerializer.Meta, "read_only_fields", ["id"]):
            with as_verified(OWNER):
                response = self.patch({"owner_email": STRANGER})
        self.assertEqual(response.status_code, 400)
        self.org.refresh_from_db()
        self.assertEqual(self.org.owner_email, OWNER)

    def test_non_object_body_is_a_400(self):
        with as_verified(OWNER):
            self.assertEqual(self.patch(["name", "Renamed"]).status_code, 400)
        self.assert_unchanged(self.org, "Acme")


class OrganizationAdminTests(TestCase):
    def setUp(self):
        self.admin = OrganizationAdmin(Organization, AdminSite())
        self.org = Organization.objects.create(name="Acme", url="https://acme.test", owner_email=OWNER)
        self.superuser = User.objects.create_superuser("root", "root@example.test", "pw")
        # Staff who hold every model permission, so the tests below show the
        # superuser rule and not merely a missing permission.
        self.staff = User.objects.create_user("staff", "staff@example.test", "pw", is_staff=True)
        self.staff.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="organizations", content_type__model="organization"
            )
        )

    def request_for(self, user):
        request = RequestFactory().get("/admin/")
        request.user = user
        return request

    def test_owner_is_read_only_on_the_change_form_for_everyone(self):
        for user in (self.superuser, self.staff):
            with self.subTest(user=user.username):
                fields = self.admin.get_readonly_fields(self.request_for(user), self.org)
                self.assertIn("owner_email", fields)

    def test_owner_can_be_set_when_a_superuser_adds_a_brand(self):
        fields = self.admin.get_readonly_fields(self.request_for(self.superuser), None)
        self.assertNotIn("owner_email", fields)

    def test_only_superusers_may_add_or_delete(self):
        self.assertTrue(self.admin.has_add_permission(self.request_for(self.superuser)))
        self.assertTrue(self.admin.has_delete_permission(self.request_for(self.superuser), self.org))
        self.assertFalse(self.admin.has_add_permission(self.request_for(self.staff)))
        self.assertFalse(self.admin.has_delete_permission(self.request_for(self.staff), self.org))
        self.assertFalse(self.admin.has_delete_permission(self.request_for(self.staff)))

    def test_bulk_delete_action_is_hidden_from_non_superusers(self):
        self.assertNotIn("delete_selected", self.admin.get_actions(self.request_for(self.staff)))
        self.assertIn("delete_selected", self.admin.get_actions(self.request_for(self.superuser)))
