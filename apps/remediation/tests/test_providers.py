"""The remediation provider contract.

CLAUDE.md §12 asks for contract tests against interfaces: the same suite must
pass for every implementation. That matters more than usual here, because the
whole point of the port is that adding Framer or Webflow is one file - and the
only way to keep that true is to pin what "one file" has to contain.

``test_a_new_provider_needs_no_changes_elsewhere`` is the load-bearing one: it
registers a provider that does not exist in this codebase and drives it through
the registry. If that ever requires touching remediation, a model, or a
migration, it will fail here first.
"""

from django.test import SimpleTestCase

from apps.remediation import providers

CONTRACT = ("make_client", "profile", "apply")


class _FramerProvider:
    """A provider that does not exist in this codebase, implemented inline.

    Stands in for the question that started this refactor: "if I have a new
    WordPress agent, do I need a new app?"
    """

    name = "framer"

    def __init__(self):
        self.applied = None

    def make_client(self, integration):
        return {"site": integration.metadata["site_id"]}

    def profile(self, client):
        return {"framework": "framer", "default_branch": "main"}

    def apply(self, client, edits, *, title, body):
        self.applied = (client, list(edits), title)
        return {"cms_revision": "rev_123"}


class ProviderRegistryTests(SimpleTestCase):
    def setUp(self):
        # Snapshot rather than re-import the adapter: importing it here would make
        # remediation depend on integrations, which already depends on remediation
        # to register - a cycle, for a test fixture.
        self._registered = dict(providers._providers)

    def tearDown(self):
        providers.reset()
        for provider in self._registered.values():
            providers.register(provider)

    def test_github_is_registered_at_startup(self):
        self.assertIn("github", providers.available())

    def test_github_implements_the_contract(self):
        adapter = providers.get("github")
        for method in CONTRACT:
            with self.subTest(method=method):
                self.assertTrue(callable(getattr(adapter, method, None)))

    def test_an_unknown_provider_is_none_not_an_error(self):
        """A deployment without an integration must degrade, not raise."""
        self.assertIsNone(providers.get("webflow"))

    def test_lookup_is_case_and_whitespace_insensitive(self):
        self.assertIsNotNone(providers.get("  GitHub "))

    def test_for_integration_resolves_by_provider_field(self):
        class Integration:
            provider = "github"

        self.assertIsNotNone(providers.for_integration(Integration()))

    def test_an_integration_with_no_adapter_is_none(self):
        class Integration:
            provider = "shopify"

        self.assertIsNone(providers.for_integration(Integration()))

    def test_a_new_provider_needs_no_changes_elsewhere(self):
        """Register a provider that does not exist here, and drive it.

        No new app, no new model, no migration - only this object. That is the
        claim docs/app-boundaries.md makes, pinned.
        """

        class Integration:
            provider = "framer"
            metadata = {"site_id": "abc"}

        framer = _FramerProvider()
        providers.register(framer)

        adapter = providers.for_integration(Integration())
        self.assertIs(adapter, framer)

        client = adapter.make_client(Integration())
        self.assertEqual(adapter.profile(client)["framework"], "framer")

        result = adapter.apply(client, ["edit"], title="Add FAQ schema", body="...")
        # Provider-shaped output is data, not schema: Framer reports a CMS
        # revision where GitHub reports a PR url, through the same call.
        self.assertEqual(result, {"cms_revision": "rev_123"})
        self.assertEqual(framer.applied[2], "Add FAQ schema")
