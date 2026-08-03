"""
GitHub App authentication.

A GitHub App authenticates in two steps:
  1. Sign a short-lived JWT (RS256) with the App's private key  -> identifies the App.
  2. Exchange that JWT for an installation access token (~1h)    -> acts on one repo.

We never persist the installation token; it's minted per action from the
private key, so there is nothing to expire badly in the database.
"""

import base64
import time

import jwt
import requests
from django.conf import settings

GITHUB_API = "https://api.github.com"
_GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def is_configured() -> bool:
    """True when the GitHub App env vars are present (App registered + wired)."""
    return bool(settings.GITHUB_APP_ID and settings.GITHUB_APP_PRIVATE_KEY)


def _private_key() -> str:
    raw = (settings.GITHUB_APP_PRIVATE_KEY or "").strip()
    if not raw:
        raise ValueError("GITHUB_APP_PRIVATE_KEY is not set")
    # Accept either a raw PEM or a base64-encoded PEM (base64 survives one-line .env).
    if "BEGIN" not in raw:
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise ValueError("GITHUB_APP_PRIVATE_KEY is neither PEM nor valid base64") from exc
    return raw


def app_jwt() -> str:
    """Build a ~9-minute JWT identifying the App (GitHub caps exp at 10 min)."""
    app_id = (settings.GITHUB_APP_ID or "").strip()
    if not app_id:
        raise ValueError("GITHUB_APP_ID is not set")
    now = int(time.time())
    payload = {
        "iat": now - 60,  # back-date 60s to tolerate clock skew
        "exp": now + 540,  # 9 minutes
        "iss": app_id,
    }
    return jwt.encode(payload, _private_key(), algorithm="RS256")


def installation_token(installation_id: int) -> str:
    """Mint a short-lived installation access token for one installation."""
    resp = requests.post(
        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
        headers={**_GITHUB_HEADERS, "Authorization": f"Bearer {app_jwt()}"},
        timeout=15,
    )
    if resp.status_code != 201:
        raise ValueError(f"Could not mint installation token (HTTP {resp.status_code}): {resp.text[:200]}")
    return resp.json()["token"]


def list_installation_repos(installation_id: int) -> list[dict]:
    """Repos this installation can access (used at callback time to pick the repo)."""
    token = installation_token(installation_id)
    resp = requests.get(
        f"{GITHUB_API}/installation/repositories",
        headers={**_GITHUB_HEADERS, "Authorization": f"Bearer {token}"},
        params={"per_page": 100},
        timeout=15,
    )
    if resp.status_code != 200:
        raise ValueError(f"Could not list installation repos (HTTP {resp.status_code}): {resp.text[:200]}")
    return resp.json().get("repositories", [])
