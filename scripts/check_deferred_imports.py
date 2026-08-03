#!/usr/bin/env python
"""Ratchet on function-level imports, so the count can only go down.

ARCHITECTURE.md §2 already forbids these ("No inline imports to dodge circular
deps - fix the dependency direction instead"). Nothing enforced it, and the
count is now 647 in analyzer alone. This makes the rule real without demanding a
big-bang cleanup: the current count is the baseline, and CI fails the moment an
app exceeds it.

Why this metric is worth gating on. A deferred import is how Python code dodges
a circular dependency at module load. One is a judgement call; hundreds are the
module graph reporting that the boundaries are wrong. It is the cheapest
available proxy for the six app-level cycles recorded in
docs/modularization-plan.md, and unlike a subjective review it moves
monotonically as those cycles are broken.

Usage:
    python scripts/check_deferred_imports.py              # check against baseline
    python scripts/check_deferred_imports.py --update     # re-record the baseline

Only lower it. Raising a baseline entry should not pass review without a reason.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = REPO_ROOT / "apps"
BASELINE_PATH = Path(__file__).resolve().parent / "deferred_imports_baseline.json"

# Migrations are generated and legitimately import inside functions.
EXCLUDED_PARTS = {"migrations", "__pycache__"}

# AppConfig.ready() imports MUST be deferred: importing a model at module level
# in apps.py raises AppRegistryNotReady, because the app registry is still being
# populated when the module is read. These are required by Django's design, not a
# cycle workaround, so counting them would penalise the ports pattern this plan
# depends on (each adapter registers from ready()).
EXCLUDED_FILENAMES = {"apps.py"}


def _is_excluded(path: Path) -> bool:
    if path.name in EXCLUDED_FILENAMES:
        return True
    return bool(EXCLUDED_PARTS.intersection(path.parts))


def count_deferred_imports(source: str) -> int:
    """Import statements that are not at module level.

    AST rather than a grep: indentation alone cannot tell a function-level import
    from one inside a module-level ``try/except ImportError``, which is a
    legitimate optional-dependency pattern and not a cycle workaround.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0

    deferred = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Import | ast.ImportFrom):
                deferred += 1
    return deferred


def scan() -> dict[str, int]:
    counts: dict[str, int] = {}
    for app_dir in sorted(APPS_DIR.iterdir()):
        if not app_dir.is_dir() or app_dir.name in EXCLUDED_PARTS:
            continue
        total = 0
        for py in app_dir.rglob("*.py"):
            if _is_excluded(py):
                continue
            total += count_deferred_imports(py.read_text(encoding="utf-8", errors="ignore"))
        counts[app_dir.name] = total
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="rewrite the baseline")
    args = parser.parse_args()

    current = scan()

    if args.update:
        BASELINE_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        print(f"Baseline written to {BASELINE_PATH.relative_to(REPO_ROOT)}")
        for app, count in sorted(current.items(), key=lambda kv: -kv[1]):
            print(f"  {app:<16} {count}")
        return 0

    if not BASELINE_PATH.exists():
        print("No baseline found. Run with --update to create one.", file=sys.stderr)
        return 1

    baseline = json.loads(BASELINE_PATH.read_text())

    regressions = []
    improvements = []
    for app, count in sorted(current.items()):
        allowed = baseline.get(app)
        if allowed is None:
            # A new app starts at zero. There is no reason to introduce one with
            # inline imports already in it.
            if count:
                regressions.append((app, 0, count))
            continue
        if count > allowed:
            regressions.append((app, allowed, count))
        elif count < allowed:
            improvements.append((app, allowed, count))

    for app, allowed, count in improvements:
        print(f"improved  {app:<16} {allowed} -> {count}")

    if regressions:
        print("\nDeferred-import ratchet FAILED\n", file=sys.stderr)
        for app, allowed, count in regressions:
            print(f"  {app:<16} baseline {allowed}, now {count}  (+{count - allowed})", file=sys.stderr)
        print(
            "\nAn inline import usually means a circular dependency. Fix the direction\n"
            "(see ARCHITECTURE.md §4 and docs/modularization-plan.md) rather than\n"
            "raising the baseline. If the import is genuinely optional-dependency\n"
            "handling, move it to a module-level try/except ImportError.",
            file=sys.stderr,
        )
        return 1

    if improvements:
        print("\nRatchet passed, and the count went down. Run --update to lock it in.")
    else:
        print("Deferred-import ratchet passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
