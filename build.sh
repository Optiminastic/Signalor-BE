#!/usr/bin/env bash
set -o errexit
set -o pipefail

pip install --upgrade pip
pip install -r requirements.txt

# Where Playwright will LOOK for browsers at runtime: a directory inside the
# installed playwright package. Render's deploy snapshot preserves this
# location (it's part of pip-installed content), unlike .ms-playwright/
# or .venv/ms-playwright/ which both turned out to be wiped on deploy.
EXPECTED_AT=$(python -c "import os, playwright; print(os.path.join(os.path.dirname(playwright.__file__), 'driver', 'package', '.local-browsers'))")
export PLAYWRIGHT_BROWSERS_PATH="$EXPECTED_AT"
mkdir -p "$EXPECTED_AT"
echo "[build.sh] PLAYWRIGHT_BROWSERS_PATH=$PLAYWRIGHT_BROWSERS_PATH"

# Install chromium + chrome-headless-shell. PLAYWRIGHT_VERBOSE_LOGS so the
# Render build log shows the actual download URLs and target directory —
# critical for diagnosing "install said OK but binary isn't there" cases.
PLAYWRIGHT_VERBOSE_LOGS=1 python -m playwright install --with-deps --force chromium chromium-headless-shell

echo "[build.sh] contents of $EXPECTED_AT after install:"
ls -la "$EXPECTED_AT" 2>&1 || echo "(directory missing)"

# Belt-and-suspenders: if the install actually wrote to one of the default
# cache locations instead of our target (we've seen install ignore the env
# var on Render), mirror those into EXPECTED_AT so runtime finds them.
# `cp -rn` won't overwrite — safe when the directories overlap.
for src in \
    "$HOME/.cache/ms-playwright" \
    "/opt/render/.cache/ms-playwright" \
    "$(python -c "import os, playwright; print(os.path.join(os.path.dirname(playwright.__file__), 'driver', '.local-browsers'))")"; do
    if [ -d "$src" ] && [ -n "$(ls -A "$src" 2>/dev/null)" ] && [ "$src" != "$EXPECTED_AT" ]; then
        echo "[build.sh] mirroring browsers from $src -> $EXPECTED_AT"
        cp -rn "$src"/. "$EXPECTED_AT/" || true
    fi
done

echo "[build.sh] final contents of $EXPECTED_AT:"
ls -la "$EXPECTED_AT"

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

    # chrome-headless-shell lives next to chromium under the same browsers
    # root. Glob for it (build number isn't hard-coded).
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
