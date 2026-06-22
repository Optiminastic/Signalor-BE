"""
Tests for the GitHub agent. The fixer transforms and repo-profile parsing are
pure (no DB), so these run as SimpleTestCase. Webhook signature verification
only reads a setting, also no DB.
"""

from django.test import SimpleTestCase, override_settings

from .services import agent, fixable, fixers, webhook
from .services.repo_profile import _profile_from_tree


class _Run:
    """Minimal stand-in for AnalysisRun (fixers only read brand_name + url)."""

    def __init__(self, url, brand_name=""):
        self.url = url
        self.brand_name = brand_name


class LlmsTxtTests(SimpleTestCase):
    def test_contains_brand_and_origin(self):
        out = fixers.build_llms_txt("Acme", "https://acme.com/pricing")
        self.assertIn("# Acme", out)
        self.assertIn("https://acme.com", out)
        self.assertNotIn("/pricing", out.split("\n")[0])  # origin only, not the path


class RobotsAiTests(SimpleTestCase):
    def test_creates_when_missing(self):
        out = fixers.inject_ai_bot_rules(None)
        self.assertIn("User-agent: GPTBot", out)
        self.assertIn("User-agent: *", out)

    def test_appends_to_existing(self):
        existing = "User-agent: *\nDisallow: /admin\n"
        out = fixers.inject_ai_bot_rules(existing)
        self.assertIn("Disallow: /admin", out)
        self.assertIn("Google-Extended", out)

    def test_idempotent(self):
        once = fixers.inject_ai_bot_rules(None)
        self.assertIsNone(fixers.inject_ai_bot_rules(once))


class JsonLdTests(SimpleTestCase):
    def test_build_graph(self):
        data = fixers.build_jsonld("Acme", "https://acme.com")
        types = {n["@type"] for n in data["@graph"]}
        self.assertEqual(types, {"Organization", "WebSite"})

    def test_inject_after_body(self):
        layout = (
            "export default function RootLayout({children}){\n"
            '  return (<html lang="en"><body className="x">{children}</body></html>)\n}'
        )
        out = fixers.inject_jsonld_into_layout(layout, fixers.build_jsonld("Acme", "https://acme.com"))
        self.assertIsNotNone(out)
        self.assertIn("application/ld+json", out)
        # script must sit inside <body>…</body>, right after the opening tag
        self.assertLess(out.index("<body"), out.index("application/ld+json"))
        self.assertLess(out.index("application/ld+json"), out.index("</body>"))

    def test_idempotent_when_present(self):
        layout = '<body><script type="application/ld+json"></script>{children}</body>'
        self.assertIsNone(fixers.inject_jsonld_into_layout(layout, fixers.build_jsonld("A", "https://a.com")))

    def test_none_without_body(self):
        self.assertIsNone(
            fixers.inject_jsonld_into_layout("export const x = 1", fixers.build_jsonld("A", "https://a.com"))
        )

    def test_escapes_apostrophe(self):
        out = fixers.inject_jsonld_into_layout(
            "<body>{children}</body>", fixers.build_jsonld("Bob's Shop", "https://b.com")
        )
        self.assertIn("\\'", out)  # apostrophe escaped for the JS string literal


class CanonicalTests(SimpleTestCase):
    def test_injects_into_metadata(self):
        layout = 'export const metadata = {\n  title: "Home",\n}\n'
        out = fixers.inject_canonical_metadata(layout, "https://acme.com")
        self.assertIsNotNone(out)
        self.assertIn("metadataBase", out)
        self.assertIn('canonical: "/"', out)

    def test_none_without_metadata_export(self):
        self.assertIsNone(fixers.inject_canonical_metadata("export default function X(){}", "https://a.com"))

    def test_idempotent_when_canonical_present(self):
        layout = "export const metadata = { alternates: { canonical: '/' } }"
        self.assertIsNone(fixers.inject_canonical_metadata(layout, "https://a.com"))


class ProfileTests(SimpleTestCase):
    def test_detects_nextjs_app_router(self):
        paths = ["package.json", "app/layout.tsx", "app/page.tsx", "public/favicon.ico"]
        pkg = '{"dependencies": {"next": "15.0.0", "react": "19.0.0"}}'
        prof = _profile_from_tree(paths, pkg)
        self.assertEqual(prof["framework"], "nextjs")
        self.assertTrue(prof["app_router"])
        self.assertEqual(prof["layout_path"], "app/layout.tsx")
        self.assertTrue(prof["has_robots_txt"] is False)

    def test_src_layout_preferred(self):
        paths = ["src/app/layout.tsx", "app/layout.tsx"]
        prof = _profile_from_tree(paths, '{"dependencies":{"next":"14"}}')
        self.assertEqual(prof["layout_path"], "src/app/layout.tsx")

    def test_unknown_without_next(self):
        prof = _profile_from_tree(["index.html"], '{"dependencies":{}}')
        self.assertEqual(prof["framework"], "unknown")
        self.assertFalse(prof["app_router"])


@override_settings(GITHUB_WEBHOOK_SECRET="testsecret")
class WebhookSignatureTests(SimpleTestCase):
    def _sign(self, body: bytes, secret="testsecret"):
        import hashlib
        import hmac

        return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_valid(self):
        body = b'{"action":"closed"}'
        self.assertTrue(webhook.verify_signature(body, self._sign(body)))

    def test_tampered(self):
        body = b'{"action":"closed"}'
        sig = self._sign(b'{"action":"opened"}')
        self.assertFalse(webhook.verify_signature(body, sig))

    def test_missing_header(self):
        self.assertFalse(webhook.verify_signature(b"{}", ""))


class WebhookMergeIdempotencyTests(SimpleTestCase):
    """A redelivered 'merged' PR event must re-crawl only once (no DB needed:
    the query manager and _trigger_recrawl are mocked)."""

    def test_redelivered_merge_recrawls_once(self):
        from unittest.mock import patch

        from .models import GithubFixJob

        class _Job:
            def __init__(self):
                self.status = GithubFixJob.Status.OPEN
                self.saves = 0

            def save(self, **kwargs):
                self.saves += 1

        job = _Job()
        payload = {"action": "closed", "pull_request": {"number": 7, "merged": True}}

        with (
            patch.object(webhook, "_trigger_recrawl") as mock_recrawl,
            patch.object(webhook, "GithubFixJob") as MockJob,
        ):
            MockJob.Status = GithubFixJob.Status
            chain = MockJob.objects.filter.return_value.select_related.return_value.order_by.return_value
            chain.first.return_value = job

            webhook._handle_pull_request(payload)  # first delivery
            webhook._handle_pull_request(payload)  # redelivery
            webhook._handle_pull_request(payload)  # redelivery

        self.assertEqual(job.status, GithubFixJob.Status.MERGED)
        self.assertEqual(mock_recrawl.call_count, 1)  # not 3
        self.assertEqual(job.saves, 1)  # status persisted once


class BuildEditsTests(SimpleTestCase):
    """build_edits with a fake client (no network/DB)."""

    class FakeClient:
        def __init__(self, files):
            self._files = files  # {path: {"text","sha"}}

        def get_file(self, path, ref=None):
            return self._files.get(path)

    def test_llms_and_robots(self):
        client = self.FakeClient({})  # nothing exists yet
        profile = {"default_branch": "main", "layout_path": "app/layout.tsx"}
        run = _Run("https://acme.com", "Acme")
        res = fixers.build_edits(client, profile, run, ["no_llms_txt", "ai_bots_blocked"])
        paths = {e.path for e in res.edits}
        self.assertEqual(paths, {"public/llms.txt", "public/robots.txt"})
        self.assertEqual(set(res.applied), {"no_llms_txt", "ai_bots_blocked"})

    def test_llms_skipped_when_present(self):
        client = self.FakeClient({"public/llms.txt": {"text": "x", "sha": "s"}})
        res = fixers.build_edits(client, {"default_branch": "main"}, _Run("https://a.com"), ["no_llms_txt"])
        self.assertEqual(res.edits, [])
        self.assertEqual(res.skipped, ["no_llms_txt"])

    def test_layout_jsonld_and_canonical_single_edit(self):
        layout_src = 'export const metadata = { title: "H" }\nexport default function L(){return(<html><body>{c}</body></html>)}'
        client = self.FakeClient({"app/layout.tsx": {"text": layout_src, "sha": "abc"}})
        profile = {"default_branch": "main", "layout_path": "app/layout.tsx"}
        res = fixers.build_edits(
            client, profile, _Run("https://acme.com", "Acme"), ["no_jsonld", "no_canonical"]
        )
        self.assertEqual(len(res.edits), 1)  # both fixes folded into one file edit
        edit = res.edits[0]
        self.assertEqual(edit.path, "app/layout.tsx")
        self.assertEqual(edit.sha, "abc")
        self.assertIn("application/ld+json", edit.new_content)
        self.assertIn("metadataBase", edit.new_content)
        self.assertEqual(set(res.applied), {"no_jsonld", "no_canonical"})


class FixableSetTests(SimpleTestCase):
    def test_structural_is_agent_fixable(self):
        for code in ("no_meta_description", "no_sitemap", "no_jsonld", "no_author", "no_h1"):
            self.assertTrue(fixable.is_agent_fixable(code), code)

    def test_offpage_and_infra_not_fixable(self):
        for code in (
            "no_https",
            "slow_load_time",
            "no_wikipedia_presence",
            "brand_not_in_ai",
            "crawl_failed",
        ):
            self.assertFalse(fixable.is_agent_fixable(code), code)

    def test_blank_not_fixable(self):
        self.assertFalse(fixable.is_agent_fixable(""))

    def test_structural_subset_of_code_fixable(self):
        self.assertTrue(fixable.STRUCTURAL.issubset(fixable.CODE_FIXABLE))

    def test_agent_attempts_all_code_fixable(self):
        # The agent now offers/attempts anything code-fixable and self-selects out
        # at fix time (cannot_fix) — not via a hardcoded structural subset.
        self.assertEqual(fixable.AGENT_FIXABLE, fixable.CODE_FIXABLE)
        self.assertTrue(fixable.STRUCTURAL.issubset(fixable.AGENT_FIXABLE))

    def test_unknown_and_content_codes_are_fixable(self):
        # Findings outside the static catalog (future/arbitrary tasks) and content
        # findings are offered to the agent — it reviews them at fix time.
        self.assertTrue(fixable.is_agent_fixable("some_future_finding_xyz"))
        self.assertTrue(fixable.is_agent_fixable("no_statistics"))
        self.assertTrue(fixable.is_agent_fixable("no_expert_quotes"))


class _FakeAgentClient:
    """Fake GitHub client for agent unit tests — no network."""

    def __init__(self, files=None, tree=None):
        self._files = files or {}
        self._tree = tree if tree is not None else list(self._files.keys())
        self.repo = "owner/repo"
        self.session = None  # search_code → AttributeError → tree fallback

    def get_file(self, path, ref=None):
        return self._files.get(path)

    def get_tree(self, ref):
        return self._tree


class AgentValidateTests(SimpleTestCase):
    def _client(self, files=None):
        return _FakeAgentClient(files or {})

    def test_empty_edits_rejected(self):
        edits, err = agent.validate_edits([], self._client(), "main")
        self.assertEqual(edits, [])
        self.assertIsNotNone(err)

    def test_path_traversal_rejected(self):
        _, err = agent.validate_edits([{"path": "../etc/passwd", "new_content": "x"}], self._client(), "main")
        self.assertIn("Invalid path", err)

    def test_empty_content_rejected(self):
        _, err = agent.validate_edits([{"path": "app/x.ts", "new_content": "   "}], self._client(), "main")
        self.assertIn("Empty content", err)

    def test_invalid_json_rejected(self):
        _, err = agent.validate_edits(
            [{"path": "data.json", "new_content": "{not json"}], self._client(), "main"
        )
        self.assertIn("not valid JSON", err)

    def test_too_many_files_rejected(self):
        raw = [{"path": f"a{i}.ts", "new_content": "x"} for i in range(agent.MAX_EDIT_FILES + 1)]
        _, err = agent.validate_edits(raw, self._client(), "main")
        self.assertIn("Too many files", err)

    def test_mass_deletion_rejected(self):
        big = "x" * 1000
        client = self._client({"app/page.tsx": {"text": big, "sha": "s"}})
        _, err = agent.validate_edits([{"path": "app/page.tsx", "new_content": "tiny"}], client, "main")
        self.assertIn("Refusing to shrink", err)

    def test_new_file_ok_sha_none(self):
        edits, err = agent.validate_edits(
            [{"path": "app/sitemap.ts", "new_content": "export default function s(){return []}"}],
            self._client(),
            "main",
        )
        self.assertIsNone(err)
        self.assertEqual(len(edits), 1)
        self.assertIsNone(edits[0].sha)

    def test_existing_file_resolves_sha(self):
        client = self._client({"app/layout.tsx": {"text": "old content here", "sha": "deadbeef"}})
        edits, err = agent.validate_edits(
            [{"path": "app/layout.tsx", "new_content": "old content here, plus a meaningful addition"}],
            client,
            "main",
        )
        self.assertIsNone(err)
        self.assertEqual(edits[0].sha, "deadbeef")


class AgentDispatchTests(SimpleTestCase):
    def _state(self):
        return {"files_read": 0, "_tree": None}

    def test_list_tree_prefix_filter(self):
        client = _FakeAgentClient(tree=["app/page.tsx", "src/lib/x.ts", "public/robots.txt"])
        out = agent.dispatch_tool(
            "list_tree", {"prefix": "app/"}, client, {"default_branch": "main"}, self._state()
        )
        self.assertIn("app/page.tsx", out)
        self.assertNotIn("src/lib/x.ts", out)

    def test_read_file_not_found(self):
        client = _FakeAgentClient({})
        out = agent.dispatch_tool(
            "read_file", {"path": "nope.ts"}, client, {"default_branch": "main"}, self._state()
        )
        self.assertIn("not found", out)

    def test_read_file_budget_exhausted(self):
        client = _FakeAgentClient({"a.ts": {"text": "x", "sha": "1"}})
        state = {"files_read": agent.MAX_FILES_READ, "_tree": None}
        out = agent.dispatch_tool("read_file", {"path": "a.ts"}, client, {"default_branch": "main"}, state)
        self.assertIn("budget exhausted", out)

    def test_search_code_falls_back_to_filename(self):
        client = _FakeAgentClient(tree=["app/sitemap.ts", "app/page.tsx"])
        out = agent.dispatch_tool(
            "search_code", {"query": "sitemap"}, client, {"default_branch": "main"}, self._state()
        )
        self.assertIn("app/sitemap.ts", out)


class AgentToolsSchemaTests(SimpleTestCase):
    def test_terminal_tools_present(self):
        names = {t["function"]["name"] for t in agent._tools()}
        self.assertIn("propose_changes", names)
        self.assertIn("cannot_fix", names)
        self.assertIn("read_file", names)

    def test_repair_edits_exists(self):
        # repair pass is wired and callable (signature contract).
        self.assertTrue(callable(agent.repair_edits))


class SandboxTests(SimpleTestCase):
    def test_package_manager_by_lockfile(self):
        import os
        import tempfile

        from .services import sandbox

        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "pnpm-lock.yaml"), "w").close()
            install, _ = sandbox._package_manager(d)
            self.assertEqual(install[0], "pnpm")
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "yarn.lock"), "w").close()
            install, _ = sandbox._package_manager(d)
            self.assertEqual(install[0], "yarn")
        with tempfile.TemporaryDirectory() as d:
            install, _ = sandbox._package_manager(d)  # nothing → npm
            self.assertEqual(install[0], "npm")

    def test_apply_writes_nested_files(self):
        import os
        import tempfile

        from .services import sandbox
        from .services.fixers import FileEdit

        with tempfile.TemporaryDirectory() as d:
            sandbox._apply([FileEdit("app/sitemap.ts", "export const x = 1\n", "s", None)], d)
            with open(os.path.join(d, "app", "sitemap.ts"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "export const x = 1\n")

    def test_disabled_via_env(self):
        from .services import sandbox

        with override_settings():  # no-op; env override below
            import os

            old = os.environ.get("GITHUB_AGENT_SANDBOX")
            os.environ["GITHUB_AGENT_SANDBOX"] = "0"
            try:
                self.assertFalse(sandbox.toolchain_available())
            finally:
                if old is None:
                    os.environ.pop("GITHUB_AGENT_SANDBOX", None)
                else:
                    os.environ["GITHUB_AGENT_SANDBOX"] = old

    def test_verify_passthrough_with_no_edits(self):
        from .services import sandbox
        from .services.fixers import FixResult

        res = FixResult(edits=[], applied=[], skipped=[])
        out, note = sandbox.verify_and_repair(None, {}, None, res, [])
        self.assertEqual(out.edits, [])
        self.assertEqual(note, "")
