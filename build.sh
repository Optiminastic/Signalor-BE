#!/usr/bin/env bash
set -o errexit
set -o pipefail

pip install --upgrade pip
pip install -r requirements.txt

# Install Playwright browsers INSIDE the Python package directory (next to
# the playwright package itself) instead of the default ~/.cache/ms-playwright.
# Render preserves the venv between build and runtime; it does NOT preserve
# ~/.cache. Without this, both chromium and chrome-headless-shell get
# installed at build time and then disappear at runtime, and every screenshot
# launch fails with "Executable doesn't exist at /opt/render/.cache/...".
# The matching PLAYWRIGHT_BROWSERS_PATH=0 on the runtime services (see
# render.yaml) tells Playwright to look in the same place.
export PLAYWRIGHT_BROWSERS_PATH=0

# Chromium for Playwright-based page screenshots (content optimisation preview).
# --with-deps installs the system libraries Chromium needs on Render's Linux image.
# Playwright 1.49+ ships chrome-headless-shell as a SEPARATE browser binary,
# used by default when launching with headless=True. If it's missing at runtime
# the launch fails with:
#   "Executable doesn't exist at .../chromium_headless_shell-XXXX/..."
# Install both in a single call with --force so a stale cache directory can't
# cause the second binary to be silently skipped on a partial cache hit.
python -m playwright install --with-deps --force chromium chromium-headless-shell

# Hard verify both binaries actually landed on disk. We have been bitten by
# the install command "succeeding" without producing chrome-headless-shell;
# failing the build here is cheaper than discovering it on the first user
# screenshot in production.
python <<'PY'
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

missing = []
with sync_playwright() as pw:
    chromium_path = Path(pw.chromium.executable_path)
    if not chromium_path.exists():
        missing.append(f"chromium: {chromium_path}")
    else:
        print(f"[playwright] chromium ok: {chromium_path}")

    # chrome-headless-shell lives next to chromium under ms-playwright/.
    # Resolve it by walking up to the browsers root and globbing for it
    # so we don't hard-code the build number.
    browsers_root = chromium_path.parents[2]
    shells = sorted(browsers_root.glob("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"))
    if not shells:
        missing.append(f"chrome-headless-shell: none found under {browsers_root}")
    else:
        print(f"[playwright] chrome-headless-shell ok: {shells[-1]}")

if missing:
    print("[playwright] missing binaries after install:", file=sys.stderr)
    for m in missing:
        print(f"  - {m}", file=sys.stderr)
    sys.exit(1)
PY

python manage.py collectstatic --no-input

# Reconcile any known migration-drift cases before running migrate. See
# scripts/reconcile_migrations.py — safe on fresh and healthy databases too.
python scripts/reconcile_migrations.py

python manage.py migrate --no-input
