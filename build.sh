#!/usr/bin/env bash
set -o errexit
set -o pipefail

pip install --upgrade pip
pip install -r requirements.txt

# Install Playwright browsers INSIDE the .venv directory. Render's
# Python service preserves .venv as the pip-cache artifact between build
# and runtime; arbitrary project-root subdirs (like /opt/render/project/
# src/.ms-playwright) DON'T survive the snapshot — we tried that and the
# runtime found an empty dir. PLAYWRIGHT_BROWSERS_PATH=0 also misbehaved
# (install vs launch resolved to different sub-paths in 1.60). An
# explicit absolute path inside .venv is what actually sticks.
# The same value must be set at runtime (render.yaml + the os.environ
# force-set in content_optimisation.py).
export PLAYWRIGHT_BROWSERS_PATH=/opt/render/project/src/.venv/ms-playwright

# Chromium for Playwright-based page screenshots (content optimisation preview).
# --with-deps installs the system libraries Chromium needs on Render's Linux image.
# Playwright 1.49+ ships chrome-headless-shell as a SEPARATE browser binary,
# used by default when launching with headless=True. If it's missing at runtime
# the launch fails with:
#   "Executable doesn't exist at .../chromium_headless_shell-XXXX/..."
# Install both in a single call with --force so a stale cache directory can't
# cause the second binary to be silently skipped on a partial cache hit.
python -m playwright install --with-deps --force chromium chromium-headless-shell

# Show what landed at the install root so build logs make it obvious if
# the dir is empty or contains a different chromium build number than
# the runtime expects.
echo "[build.sh] installed browsers at $PLAYWRIGHT_BROWSERS_PATH:"
ls -la "$PLAYWRIGHT_BROWSERS_PATH" 2>&1 || echo "(directory missing)"

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
