"""GitHub adapter for ``apps.remediation.providers``.

Everything GitHub-specific about applying a fix lives here: branching, committing
per file, and opening the pull request. The planner that decided *what* to change
knows none of it.

This is the shape a Framer or Webflow adapter takes - three methods, no changes
anywhere else.
"""

from __future__ import annotations

import logging

from .client import GithubClient
from .repo_profile import detect_profile

logger = logging.getLogger("apps")

name = "github"

BRANCH_PREFIX = "signalor/fix-"


def make_client(integration):
    """Build a client from the stored install details.

    Reads from ``metadata`` so this works both today (``GithubInstallation``) and
    after that table folds into ``Integration`` - see docs/app-boundaries.md §10.1.
    """
    meta = getattr(integration, "metadata", None) or {}
    installation_id = meta.get("installation_id") or getattr(integration, "installation_id", None)
    repo = meta.get("repo_full_name") or getattr(integration, "repo_full_name", "")
    if not installation_id or not repo:
        raise ValueError("GitHub integration is missing installation_id or repo_full_name")
    return GithubClient(installation_id, repo)


def profile(client) -> dict:
    """Framework, default branch and layout, for the planner's system prompt."""
    return detect_profile(client)


def apply(client, edits: list, *, title: str, body: str) -> dict:
    """Branch, commit each edit, open a PR. Returns the PR details.

    One commit per file rather than a single tree write: it keeps the PR readable
    and means a single bad edit fails on its own line instead of poisoning the
    whole changeset.

    Raises on failure so the caller records the error rather than guessing whether
    a partial write landed - a half-open PR is worse than a reported failure.
    """
    base = profile(client).get("default_branch") or client.get_default_branch() or "main"
    branch = f"{BRANCH_PREFIX}{abs(hash(title)) % 10_000_000}"

    base_sha = client.get_branch_sha(base)
    client.create_branch(branch, base_sha)

    for edit in edits:
        client.put_file(
            edit.path,
            edit.content,
            message=f"{title}: {edit.path}",
            branch=branch,
            sha=getattr(edit, "sha", None),
        )

    pr = client.create_pull_request(title=title, head=branch, base=base, body=body)
    return {
        "branch": branch,
        "pr_number": pr.get("number"),
        "pr_url": pr.get("html_url", ""),
    }
