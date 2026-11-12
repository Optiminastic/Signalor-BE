"""
Contact finders (Hunter, Apollo) for sites with no public contact email.

Nothing is looked up until the owner asks, and nothing is used until the
owner picks a person. Hunter returns emails directly; Apollo's search
returns people without emails, and an email is revealed (1 Apollo credit)
only for the person the owner picks. No network: the HTTP calls are faked
with responses shaped like the providers' documented examples.

Run: python manage.py test apps.growth_agent.tests.test_contact_finders --settings=config.settings.test
"""

from unittest import mock

from django.test import SimpleTestCase, override_settings

from ..models import AgentActionLog, GrowthTask, OutreachContact
from ..services.outreach.contact_finders import apollo, hunter, registry
from ..services.outreach.contact_finders.base import Candidate, FinderError
from .test_api import OTHER_EMAIL, PRO_EMAIL
from .test_owner_decides import ASYNC_RUN, OwnerDecidesTestCase

HUNTER_RESPONSE = {
    "data": {
        "domain": "site.com",
        "emails": [
            {
                "value": "jane@site.com",
                "type": "personal",
                "confidence": 94,
                "first_name": "Jane",
                "last_name": "Doe",
                "position": "Content Editor",
            },
            {
                "value": "info@site.com",
                "type": "generic",
                "confidence": 60,
                "first_name": None,
                "last_name": None,
            },
        ],
    }
}
APOLLO_SEARCH = {
    "people": [
        {
            "id": "p1",
            "first_name": "Sam",
            "last_name_obfuscated": "Ro***s",
            "title": "Head of Content",
            "has_email": True,
        },
        {
            "id": "p2",
            "first_name": "Ann",
            "last_name_obfuscated": "Le***",
            "title": "Engineer",
            "has_email": False,
        },
    ]
}
APOLLO_MATCH = {
    "person": {"id": "p1", "email": "sam@site.com", "email_status": "verified", "name": "Sam Rogers"}
}


def response(body, status=200):
    reply = mock.Mock(status_code=status)
    reply.json.return_value = body
    reply.ok = status < 400
    return reply


class HunterTests(SimpleTestCase):
    def test_parses_people_with_their_emails_and_skips_generic_inboxes(self):
        with mock.patch.object(hunter.requests, "get", return_value=response(HUNTER_RESPONSE)) as get:
            found = hunter.find("site.com", "key-123")
        self.assertEqual(
            found,
            [Candidate("hunter", "jane@site.com", "Jane Doe", "Content Editor", "jane@site.com", "94%")],
        )
        _, kwargs = get.call_args
        self.assertEqual(kwargs["params"]["domain"], "site.com")
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "key-123")
        self.assertNotIn("api_key", kwargs["params"])  # the key never goes in a URL

    def test_an_error_response_raises(self):
        with mock.patch.object(hunter.requests, "get", return_value=response({"errors": []}, 401)):
            with self.assertRaises(FinderError):
                hunter.find("site.com", "bad")


class ApolloTests(SimpleTestCase):
    def test_search_lists_people_who_have_an_email_without_revealing_it(self):
        with mock.patch.object(apollo.requests, "post", return_value=response(APOLLO_SEARCH)) as post:
            found = apollo.find("site.com", "key-abc")
        self.assertEqual(found, [Candidate("apollo", "p1", "Sam Ro***s", "Head of Content", "", "")])
        args, kwargs = post.call_args
        self.assertEqual(args[0], apollo.SEARCH_URL)
        self.assertEqual(kwargs["headers"]["x-api-key"], "key-abc")
        self.assertEqual(kwargs["json"]["q_organization_domains_list"], ["site.com"])

    def test_reveal_returns_the_work_email_for_one_person(self):
        with mock.patch.object(apollo.requests, "post", return_value=response(APOLLO_MATCH)) as post:
            self.assertEqual(apollo.reveal("p1", "key-abc"), "sam@site.com")
        args, kwargs = post.call_args
        self.assertEqual(args[0], apollo.MATCH_URL)
        self.assertEqual(kwargs["json"], {"id": "p1"})

    def test_reveal_without_an_email_raises(self):
        with mock.patch.object(
            apollo.requests, "post", return_value=response({"person": {"id": "p1", "email": None}})
        ):
            with self.assertRaises(FinderError):
                apollo.reveal("p1", "key-abc")


class RegistryTests(SimpleTestCase):
    @override_settings(HUNTER_API_KEY="", APOLLO_API_KEY="")
    def test_no_keys_means_no_finders(self):
        self.assertEqual(registry.configured(), [])

    @override_settings(HUNTER_API_KEY="h", APOLLO_API_KEY="a")
    def test_one_failing_finder_does_not_hide_the_others(self):
        with (
            mock.patch.object(hunter, "find", side_effect=FinderError("down")),
            mock.patch.object(
                apollo, "find", return_value=[Candidate("apollo", "p1", "Sam", "Editor", "", "")]
            ),
        ):
            result = registry.find_all("site.com")
        self.assertEqual([c.ref for c in result.candidates], ["p1"])
        self.assertEqual(result.providers, ["hunter", "apollo"])
        self.assertEqual(result.errors, ["hunter"])


@override_settings(HUNTER_API_KEY="h", APOLLO_API_KEY="a")
class FindAndPickTests(OwnerDecidesTestCase):
    def setUp(self):
        super().setUp()
        self.run_prepare(contact=None)  # no public email: waiting for the owner

    def find(self, email=PRO_EMAIL):
        self._body = {}
        path = f"/api/growth/programs/{self.pro_org.slug}/tasks/{self.task.id}/find-contacts/"
        with (
            mock.patch.object(
                hunter,
                "find",
                return_value=[
                    Candidate("hunter", "jane@site.com", "Jane Doe", "Editor", "jane@site.com", "94%")
                ],
            ),
            mock.patch.object(
                apollo,
                "find",
                return_value=[Candidate("apollo", "p1", "Sam Ro***s", "Head of Content", "", "")],
            ),
        ):
            return self.signed("POST", path, email)

    def test_finding_lists_people_from_every_finder_and_logs_it(self):
        response = self.find()
        self.assertEqual(response.status_code, 200)
        names = [c["name"] for c in response.json()["candidates"]]
        self.assertEqual(names, ["Jane Doe", "Sam Ro***s"])
        self.task.refresh_from_db()
        self.assertEqual(len(self.task.result_payload["needs_contact"]["candidates"]), 2)
        self.assertTrue(AgentActionLog.objects.filter(action="contacts_found").exists())

    def test_picking_a_hunter_person_uses_their_email(self):
        self.find()
        response, run = self.contact_endpoint({"candidate": {"provider": "hunter", "ref": "jane@site.com"}})
        self.assertEqual(response.status_code, 202)
        run.delay.assert_called_once()
        contact = OutreachContact.objects.get(organization=self.pro_org, email="jane@site.com")
        self.assertEqual(contact.source_url, "Found with Hunter")

    def test_picking_an_apollo_person_reveals_only_their_email(self):
        self.find()
        with mock.patch.object(apollo, "reveal", return_value="sam@site.com") as reveal:
            response, _ = self.contact_endpoint({"candidate": {"provider": "apollo", "ref": "p1"}})
        self.assertEqual(response.status_code, 202)
        reveal.assert_called_once_with("p1", "a")
        self.assertTrue(
            OutreachContact.objects.filter(email="sam@site.com", source_url="Found with Apollo").exists()
        )

    def test_a_person_not_from_the_list_is_refused(self):
        self.find()
        response, run = self.contact_endpoint(
            {"candidate": {"provider": "hunter", "ref": "attacker@evil.com"}}
        )
        self.assertEqual(response.status_code, 409)
        run.delay.assert_not_called()

    def test_a_failed_reveal_is_reported_and_nothing_is_queued(self):
        self.find()
        with mock.patch.object(apollo, "reveal", side_effect=FinderError("no credits")):
            response, run = self.contact_endpoint({"candidate": {"provider": "apollo", "ref": "p1"}})
        self.assertEqual(response.status_code, 502)
        run.delay.assert_not_called()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, GrowthTask.Status.AWAITING_APPROVAL)

    def test_another_brands_owner_cannot_look_up_contacts(self):
        self.assertIn(self.find(email=OTHER_EMAIL).status_code, (403, 404))

    @override_settings(HUNTER_API_KEY="", APOLLO_API_KEY="")
    def test_with_no_finders_set_up_the_answer_says_so(self):
        response = self.find()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["providers"], [])
        self.assertEqual(response.json()["candidates"], [])
