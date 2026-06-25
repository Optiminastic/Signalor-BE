"""
Verify GitHub App credentials end to end without needing the full install flow.

    python manage.py github_smoketest                 # checks App ID + private key
    python manage.py github_smoketest <installation>  # also mints a token + reads the repo

No args: signs an App JWT and calls GET /app - proves GITHUB_APP_ID +
GITHUB_APP_PRIVATE_KEY are valid. With an installation id: also mints an
installation token, lists repos, and reads the file tree of the first repo.
"""

import requests
from django.core.management.base import BaseCommand

from apps.github_agent.services import auth
from apps.github_agent.services.client import GithubClient


class Command(BaseCommand):
    help = "Verify GitHub App credentials (App JWT, installation token, repo read)."

    def add_arguments(self, parser):
        parser.add_argument("installation_id", nargs="?", type=int, default=None)

    def handle(self, *args, **options):
        ok = self.style.SUCCESS
        warn = self.style.WARNING
        err = self.style.ERROR

        if not auth.is_configured():
            self.stdout.write(
                err("[X] Not configured - GITHUB_APP_ID and/or GITHUB_APP_PRIVATE_KEY are missing.")
            )
            return
        self.stdout.write(ok("[OK] Env present: GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY"))

        # 1. App JWT → GET /app  (proves the App ID + private key match)
        try:
            jwt_token = auth.app_jwt()
            resp = requests.get(
                f"{auth.GITHUB_API}/app",
                headers={**auth._GITHUB_HEADERS, "Authorization": f"Bearer {jwt_token}"},
                timeout=15,
            )
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(err(f"[X] Could not sign/send App JWT: {exc}"))
            return

        if resp.status_code != 200:
            self.stdout.write(err(f"[X] GET /app failed (HTTP {resp.status_code}): {resp.text[:200]}"))
            self.stdout.write(warn("  -> App ID or private key is wrong, or the key was rotated."))
            return

        app = resp.json()
        self.stdout.write(ok(f"[OK] App authenticated: {app.get('name')} (slug: {app.get('slug')})"))
        self.stdout.write(f"  App ID: {app.get('id')}  *  installs: {app.get('installations_count', '?')}")

        installation_id = options["installation_id"]
        if not installation_id:
            self.stdout.write(
                warn(
                    "\nNo installation id given. Install the App on a repo, then re-run:\n"
                    "  python manage.py github_smoketest <installation_id>\n"
                    "(the installation id is in the callback URL after you install.)"
                )
            )
            return

        # 2. Installation token + repo read
        try:
            repos = auth.list_installation_repos(installation_id)
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(err(f"[X] Could not mint installation token: {exc}"))
            return

        names = [r.get("full_name") for r in repos if r.get("full_name")]
        self.stdout.write(
            ok(f"[OK] Installation token works - {len(names)} repo(s): {', '.join(names) or '-'}")
        )
        if not names:
            return

        try:
            client = GithubClient(installation_id, names[0])
            branch = client.get_default_branch()
            tree = client.get_tree(branch)
            self.stdout.write(
                ok(f"[OK] Read {names[0]}@{branch}: {len(tree)} files. e.g. {', '.join(tree[:5])}")
            )
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(err(f"[X] Could not read repo tree: {exc}"))
