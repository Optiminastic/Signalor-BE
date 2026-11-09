"""
Pure execution units: article rendering/escaping, safety rules, GitHub publishing helpers.

Run: python manage.py test apps.growth_agent.tests.test_execution_units --settings=config.settings.test
"""

from datetime import date
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from ..services.execution import article as art
from ..services.execution import safety
from ..services.execution.publishers import github as gh
from ..services.execution.publishers.base import PublishError

ASK = "apps.growth_agent.services.execution.safety.ask_structured"
LONG_PARAGRAPH = " ".join(["Plain useful explanation of the topic for buyers."] * 12)


def make_article(**overrides) -> art.ArticleDraft:
    data = {
        "title": "What is GEO? A practical guide",
        "meta_description": "GEO explained simply.",
        "answer": "GEO is optimising content so AI engines cite it; Signalor measures it.",
        "sections": [art.Section(heading=f"Part {i}", paragraphs=[LONG_PARAGRAPH]) for i in range(4)],
        "faq": [art.FaqItem(question="Is GEO new?", answer="Yes, it grew with AI search.")],
    }
    data.update(overrides)
    return art.ArticleDraft(**data)


class ArticleRenderingTests(SimpleTestCase):
    def test_html_escapes_model_output(self):
        html = art.render_html(make_article(answer='<script>alert("x")</script>'))
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_jsonld_cannot_close_script_tag(self):
        evil = art.FaqItem(question="q", answer="</script><img src=x>")
        html = art.render_html(make_article(faq=[evil]))
        self.assertEqual(html.count("</script>"), 1)  # only the real closing tag
        self.assertIn('"@type": "FAQPage"', html)

    def test_markdown_escapes_and_writes_frontmatter(self):
        md = art.render_markdown(
            make_article(answer="Use *stars* and <b>tags</b>"), {"title": 'A "quoted" title'}
        )
        self.assertTrue(md.startswith('---\ntitle: "A \\"quoted\\" title"\n---'))
        self.assertIn("\\*stars\\*", md)
        self.assertIn("\\<b\\>", md)

    def test_slugify(self):
        self.assertEqual(art.slugify("What is GEO? A Guide!"), "what-is-geo-a-guide")
        self.assertEqual(art.slugify("???"), "article")


class SafetyRuleTests(SimpleTestCase):
    def test_good_article_has_no_rule_issues(self):
        self.assertEqual(safety.rule_issues(make_article()), [])

    def test_short_banned_phrase_and_urls_are_flagged(self):
        bad = make_article(
            sections=[art.Section(heading="Only", paragraphs=["Guaranteed results, see https://x.com"])]
        )
        issues = " ".join(safety.rule_issues(bad))
        self.assertIn("Too short", issues)
        self.assertIn("guaranteed", issues)
        self.assertIn("URLs", issues)
        self.assertIn("at least 3 sections", issues)

    @mock.patch(ASK, return_value=None)
    def test_fact_check_unavailable_fails_closed(self, _ask):
        verdict = safety.check_article(make_article(), "Signalor", "facts")
        self.assertFalse(verdict.passed)

    @mock.patch(ASK)
    def test_fact_check_issues_block_publishing(self, ask):
        ask.return_value = safety.FactCheck(passed=False, issues=["Claims 10,000 customers"])
        verdict = safety.check_article(make_article(), "Signalor", "facts")
        self.assertEqual(verdict.issues, ("Claims 10,000 customers",))

    @mock.patch(ASK)
    def test_page_that_never_names_the_brand_is_blocked(self, ask):
        verdict = safety.check_article(make_article(answer="GEO is about AI citations."), "Signalor", "facts")
        self.assertFalse(verdict.passed)
        self.assertIn("never mentions Signalor", verdict.issues[0])
        ask.assert_not_called()

    @mock.patch(ASK)
    def test_rule_failure_skips_llm(self, ask):
        safety.check_article(make_article(sections=[]), "Signalor", "facts")
        ask.assert_not_called()


class GithubHelperTests(SimpleTestCase):
    def test_find_content_dir_prefers_blog_folder_with_most_posts(self):
        paths = [
            "README.md",
            "docs/setup.md",
            "content/blog/a.mdx",
            "content/blog/b.mdx",
            "node_modules/x/blog/readme.md",
            "app/page.tsx",
        ]
        self.assertEqual(gh.find_content_dir(paths), ("content/blog", ".mdx", "content/blog/a.mdx"))

    def test_no_content_folder_returns_none(self):
        self.assertIsNone(gh.find_content_dir(["app/page.tsx", "README.md", "docs/setup.md"]))

    def test_frontmatter_keys_and_mapping(self):
        keys = gh.frontmatter_keys('---\ntitle: "x"\npublishedAt: 2024-01-01\nauthor: me\n---\nbody')
        self.assertEqual(keys, ["title", "publishedAt", "author"])
        fm = gh.build_frontmatter(keys, make_article(), date(2026, 11, 9))
        self.assertEqual(fm["publishedAt"], "2026-11-09")
        self.assertNotIn("author", fm)  # never invent values the model can't know
        self.assertIn("description", fm)


class FakeGithubClient:
    def __init__(self, existing=None):
        self.tree = ["content/blog/old-post.mdx", "app/page.tsx"]
        self.files = {
            "content/blog/old-post.mdx": {"text": '---\ntitle: "Old"\ndate: 2024-01-01\n---\n', "sha": "s"}
        }
        self.files.update(existing or {})
        self.put = []
        self.merged = []

    def get_default_branch(self):
        return "main"

    def get_tree(self, ref):
        return self.tree

    def get_file(self, path, ref=None):
        return self.files.get(path)

    def get_branch_sha(self, branch):
        return "abc"

    def create_branch(self, name, sha):
        self.branch = name

    def put_file(self, path, text, message, branch, sha=None):
        self.put.append((path, text, branch))

    def create_pull_request(self, title, head, base, body):
        return {"number": 7, "html_url": "https://github.com/o/r/pull/7"}

    def merge_pull_request(self, number, commit_title):
        self.merged.append(number)
        return {"merged": True}


class GithubPublisherTests(SimpleTestCase):
    def setUp(self):
        self.installation = SimpleNamespace(installation_id=1, repo_full_name="o/r", default_branch="main")

    def test_publishes_into_content_folder_and_merges(self):
        client = FakeGithubClient()
        result = gh.GithubPublisher(self.installation, client=client).publish(make_article(), "what-is-geo")
        path, text, branch = client.put[0]
        self.assertEqual(path, "content/blog/what-is-geo.mdx")
        self.assertIn('date: "', text)  # mirrors the sample post's frontmatter keys
        self.assertTrue(branch.startswith(gh.BRANCH_PREFIX))
        self.assertEqual(client.merged, [7])
        self.assertEqual(result.reference, "pr:7")

    def test_refuses_to_overwrite_existing_page(self):
        client = FakeGithubClient(existing={"content/blog/what-is-geo.mdx": {"text": "", "sha": "z"}})
        with self.assertRaises(PublishError):
            gh.GithubPublisher(self.installation, client=client).publish(make_article(), "what-is-geo")
        self.assertEqual(client.put, [])
