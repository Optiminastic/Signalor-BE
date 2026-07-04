"""
Detect just enough about a repo for the fixers to know *where* code goes.

v1 targets Next.js (app router). We read the file tree + package.json once and
cache the result on the installation (``repo_profile`` JSON), refreshing when
stale. Pure parsing lives in ``_profile_from_tree`` so it's unit-testable
without network.
"""

import json
import logging

logger = logging.getLogger("apps")

# Candidate root-layout locations, most specific first.
_LAYOUT_CANDIDATES = [
    "src/app/layout.tsx",
    "src/app/layout.jsx",
    "src/app/layout.ts",
    "src/app/layout.js",
    "app/layout.tsx",
    "app/layout.jsx",
    "app/layout.ts",
    "app/layout.js",
]


def _profile_from_tree(paths: list[str], package_json_text: str | None) -> dict:
    """Pure: derive the profile from a flat path list + package.json contents."""
    path_set = set(paths)

    deps: dict = {}
    if package_json_text:
        try:
            pkg = json.loads(package_json_text)
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        except (ValueError, AttributeError):
            deps = {}

    is_next = "next" in deps
    framework = "nextjs" if is_next else "unknown"

    layout_path = next((c for c in _LAYOUT_CANDIDATES if c in path_set), "")
    app_router = bool(layout_path)
    # "src/app/..." layout means the public dir + src convention; public/ is still root-level.
    has_public_dir = any(p.startswith("public/") for p in paths)

    return {
        "framework": framework,
        "app_router": app_router,
        "layout_path": layout_path,
        "public_dir": "public" if has_public_dir else "public",  # Next serves /public regardless
        "has_llms_txt": "public/llms.txt" in path_set,
        "has_robots_txt": "public/robots.txt" in path_set,
        "robots_ts_path": next(
            (p for p in ("app/robots.ts", "src/app/robots.ts", "app/robots.js") if p in path_set),
            "",
        ),
        "next_version": str(deps.get("next", "")),
    }


def detect_profile(client) -> dict:
    """Fetch tree + package.json via the GitHub client and build the profile."""
    default_branch = client.get_default_branch()
    paths = client.get_tree(default_branch)
    pkg = client.get_file("package.json", ref=default_branch)
    profile = _profile_from_tree(paths, pkg["text"] if pkg else None)
    profile["default_branch"] = default_branch
    return profile
