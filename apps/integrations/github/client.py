"""
Thin GitHub REST wrapper scoped to one repo, authenticated with an installation
access token. Only the handful of endpoints the fixer flow needs: read the tree,
read/write files via the Contents API, branch, and open a PR.

All methods raise ValueError on unexpected HTTP responses so the orchestrator can
fail the job with a readable message (matches the integrations services style).
"""

import base64
import logging

import requests

from .auth import _GITHUB_HEADERS, GITHUB_API, installation_token

logger = logging.getLogger("apps")


class GithubClient:
    def __init__(self, installation_id: int, repo_full_name: str):
        if not repo_full_name or "/" not in repo_full_name:
            raise ValueError(f"Invalid repo_full_name: {repo_full_name!r}")
        self.repo = repo_full_name
        self._token = installation_token(installation_id)
        self.session = requests.Session()
        self.session.headers.update({**_GITHUB_HEADERS, "Authorization": f"Bearer {self._token}"})

    # -- low-level ---------------------------------------------------------
    def _repo_url(self, path: str) -> str:
        return f"{GITHUB_API}/repos/{self.repo}{path}"

    def _get(self, path: str, **kwargs):
        return self.session.get(self._repo_url(path), timeout=20, **kwargs)

    # -- repo metadata -----------------------------------------------------
    def get_repo(self) -> dict:
        resp = self._get("")
        if resp.status_code != 200:
            raise ValueError(f"get_repo failed (HTTP {resp.status_code}): {resp.text[:200]}")
        return resp.json()

    def get_default_branch(self) -> str:
        return self.get_repo().get("default_branch", "main")

    def search_code(self, query: str, limit: int = 10) -> list[str]:
        """File paths in this repo matching ``query``, or [] if search is unavailable.

        The planning agent used to build this URL itself off ``client.session``,
        which was its only piece of GitHub knowledge and the one thing keeping it
        vendor-bound. Owning it here means any provider can offer the same
        capability under the same name.

        Returns [] rather than raising: code search is a hint for the planner, and
        the caller already falls back to matching against the file tree.
        """
        try:
            resp = self.session.get(
                f"{GITHUB_API}/search/code",
                params={"q": f"{query} repo:{self.repo}", "per_page": limit},
                timeout=20,
            )
            if resp.status_code == 200:
                return [item["path"] for item in resp.json().get("items", [])]
        except Exception as exc:  # noqa: BLE001
            logger.debug("code search failed (%s); caller falls back to tree match", exc)
        return []

    def get_tree(self, ref: str) -> list[str]:
        """Return a flat list of file paths in the repo at ``ref`` (recursive)."""
        resp = self._get(f"/git/trees/{ref}", params={"recursive": "1"})
        if resp.status_code != 200:
            raise ValueError(f"get_tree failed (HTTP {resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        return [item["path"] for item in data.get("tree", []) if item.get("type") == "blob"]

    def get_file(self, path: str, ref: str | None = None) -> dict | None:
        """Return ``{"text": str, "sha": str}`` for a file, or None if it doesn't exist."""
        params = {"ref": ref} if ref else {}
        resp = self._get(f"/contents/{path}", params=params)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise ValueError(f"get_file {path} failed (HTTP {resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        if isinstance(data, list):  # path is a directory
            return None
        raw = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
        return {"text": raw, "sha": data.get("sha", "")}

    # -- branch + write ----------------------------------------------------
    def get_branch_sha(self, branch: str) -> str:
        resp = self._get(f"/git/ref/heads/{branch}")
        if resp.status_code != 200:
            raise ValueError(f"get_branch_sha {branch} failed (HTTP {resp.status_code}): {resp.text[:200]}")
        return resp.json()["object"]["sha"]

    def create_branch(self, new_branch: str, from_sha: str) -> None:
        resp = self.session.post(
            self._repo_url("/git/refs"),
            json={"ref": f"refs/heads/{new_branch}", "sha": from_sha},
            timeout=20,
        )
        if resp.status_code not in (200, 201):
            raise ValueError(
                f"create_branch {new_branch} failed (HTTP {resp.status_code}): {resp.text[:200]}"
            )

    def put_file(self, path: str, text: str, message: str, branch: str, sha: str | None = None) -> None:
        """Create or update a file (Contents API). Pass ``sha`` to update in place."""
        body = {
            "message": message,
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha
        resp = self.session.put(self._repo_url(f"/contents/{path}"), json=body, timeout=20)
        if resp.status_code not in (200, 201):
            raise ValueError(f"put_file {path} failed (HTTP {resp.status_code}): {resp.text[:200]}")

    def create_pull_request(self, title: str, head: str, base: str, body: str) -> dict:
        resp = self.session.post(
            self._repo_url("/pulls"),
            json={"title": title, "head": head, "base": base, "body": body},
            timeout=20,
        )
        if resp.status_code not in (200, 201):
            raise ValueError(f"create_pull_request failed (HTTP {resp.status_code}): {resp.text[:300]}")
        return resp.json()

    def ensure_label(self, name: str, color: str, description: str = "") -> None:
        """Create a repo label if it isn't there yet.

        GitHub rejects adding a label that doesn't exist, so it has to be created
        first. A 422 means another request already created it, which is success
        for our purposes.
        """
        resp = self.session.post(
            self._repo_url("/labels"),
            json={"name": name, "color": color, "description": description[:100]},
            timeout=20,
        )
        if resp.status_code not in (200, 201, 422):
            raise ValueError(f"ensure_label {name} failed (HTTP {resp.status_code}): {resp.text[:200]}")

    def add_labels(self, issue_number: int, labels: list[str]) -> None:
        """Attach labels to a PR (PRs are issues for this endpoint)."""
        if not labels:
            return
        resp = self.session.post(
            self._repo_url(f"/issues/{issue_number}/labels"),
            json={"labels": labels},
            timeout=20,
        )
        if resp.status_code not in (200, 201):
            raise ValueError(f"add_labels failed (HTTP {resp.status_code}): {resp.text[:200]}")
