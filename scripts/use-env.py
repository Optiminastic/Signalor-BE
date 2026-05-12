#!/usr/bin/env python
"""Switch the active Django env: copy env/<name>.env to .env at the project root.

Usage:
    python scripts/use-env.py local
    python scripts/use-env.py staging
    python scripts/use-env.py production
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

VALID = ("local", "staging", "production")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in VALID:
        print(f"usage: python scripts/use-env.py {{{'|'.join(VALID)}}}", file=sys.stderr)
        return 1

    target = sys.argv[1]
    project_root = Path(__file__).resolve().parent.parent
    src = project_root / "env" / f"{target}.env"
    dst = project_root / ".env"

    if not src.exists():
        print(f"error: {src} not found", file=sys.stderr)
        return 1

    shutil.copyfile(src, dst)
    print(f"-> .env <- env/{target}.env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
