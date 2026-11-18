"""
GitHub adapter for Next.js sites connected through the Signalor GitHub App.

Publishing = add one Markdown/MDX file to the repo's existing content folder on
a new branch, open a PR, and squash-merge it (fully autonomous; the site's own
deploy pipeline ships it). The file reuses the frontmatter keys of an existing
post so it renders with the site's own templates. Without a recognisable
content folder the adapter refuses rather than guessing where pages go.
"""

from __future__ import annotations

import re
from datetime import date
from posixpath import dirname, splitext

from apps.integrations.github.client import GithubClient

from ..article import ArticleDraft, render_markdown
from .base import PublishError, PublishResult

CONTENT_EXTENSIONS = (".mdx", ".md")
CONTENT_DIR_HINTS = ("blog", "posts", "articles", "guides", "insights", "resources", "content")
IGNORED_PARTS = ("node_modules", ".github", ".next", "public", "test", "__tests__")
BRANCH_PREFIX = "signalor/growth-"
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)
_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:", re.MULTILINE)


def _is_content_file(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(CONTENT_EXTENSIONS) and not any(f"/{p}/" in f"/{lowered}" for p in IGNORED_PARTS)


def find_content_dir(paths: list[str]) -> tuple[str, str, str] | None:
    """Pick the folder holding the most posts, preferring blog-like names.
    Returns (directory, extension, sample_file) or None."""
    counts: dict[str, list[str]] = {}
    for path in paths:
        if _is_content_file(path) and not path.lower().endswith("readme.md"):
            counts.setdefault(dirname(path), []).append(path)
    candidates = [
        (sum(h in d.lower() for h in CONTENT_DIR_HINTS), len(files), d, files)
        for d, files in counts.items()
        if any(h in d.lower() for h in CONTENT_DIR_HINTS)
    ]
    if not candidates:
        return None
    _, _, directory, files = max(candidates)
    sample = sorted(files)[0]
    return directory, splitext(sample)[1], sample


def frontmatter_keys(text: str) -> list[str]:
    match = _FRONTMATTER.match(text or "")
    return _KEY.findall(match.group(1)) if match else []


def build_frontmatter(keys: list[str], article: ArticleDraft, today: date) -> dict[str, str]:
    values = {
        "title": article.title,
        "description": article.meta_description,
        "excerpt": article.meta_description,
        "summary": article.meta_description,
        "date": today.isoformat(),
        "publishedAt": today.isoformat(),
        "published": "true",
    }
    chosen = {k: values[k] for k in keys if k in values}
    chosen.setdefault("title", article.title)
    chosen.setdefault("description", article.meta_description)
    return chosen


class GithubPublisher:
    name = "github"

    def __init__(self, installation, client: GithubClient | None = None):
        self.installation = installation
        self.client = client or GithubClient(installation.installation_id, installation.repo_full_name)

    def publish(self, article: ArticleDraft, slug: str) -> PublishResult:
        try:
            return self._publish(article, slug)
        except ValueError as exc:  # GithubClient raises ValueError on HTTP failures
            raise PublishError(str(exc)) from exc

    def _publish(self, article: ArticleDraft, slug: str) -> PublishResult:
        base = self.installation.default_branch or self.client.get_default_branch()
        found = find_content_dir(self.client.get_tree(base))
        if found is None:
            raise PublishError("No blog or content folder found in the repo to publish into.")
        directory, extension, sample = found
        path = f"{directory}/{slug}{extension}"
        if self.client.get_file(path, ref=base):
            raise PublishError(f"A page already exists at {path}.")
        sample_file = self.client.get_file(sample, ref=base) or {}
        keys = frontmatter_keys(sample_file.get("text", ""))
        body = render_markdown(article, build_frontmatter(keys, article, date.today()))

        branch = f"{BRANCH_PREFIX}{slug}"
        self.client.create_branch(branch, self.client.get_branch_sha(base))
        self.client.put_file(path, body, f"Add page: {article.title}", branch)
        pr = self.client.create_pull_request(
            title=f"Add page: {article.title}",
            head=branch,
            base=base,
            body="Published autonomously by the Signalor Growth Agent (passed brand-safety checks).",
        )
        self.client.merge_pull_request(pr["number"], f"Add page: {article.title}")
        return PublishResult(url=pr.get("html_url", ""), reference=f"pr:{pr['number']}")
