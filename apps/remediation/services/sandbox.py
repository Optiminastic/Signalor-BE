"""
Pre-PR build verification + self-repair.

Before the orchestrator opens a PR, we clone the repo into a temp dir, write the
proposed edits onto the working tree, install deps, and run the project's
TypeScript type-check. If it fails, the build errors are fed back to the fix
agent (``agent.repair_edits``) for a corrected pass — repeated up to a small cap.
Only edits that type-check clean reach the PR; if they never compile, the edits
are cleared so the orchestrator fails the job instead of opening a broken PR.

The whole step is best-effort: if git / node / a package manager isn't on the
host (e.g. the prod Python image), verification is skipped and the edits pass
through unchanged — exactly the old behaviour.
"""

import logging
import os
import shutil
import subprocess
import tempfile

from . import agent as fix_agent
from .fixers import FileEdit, FixResult

logger = logging.getLogger("apps")

MAX_REPAIRS = 2
CLONE_TIMEOUT = 180
INSTALL_TIMEOUT = 420
CHECK_TIMEOUT = 240
MAX_ERROR_CHARS = 6000


def _enabled() -> bool:
    return os.getenv("GITHUB_AGENT_SANDBOX", "1") != "0"


def _resolve(cmd: list[str]) -> list[str] | None:
    """Resolve the executable (handles Windows .cmd shims). None if not found."""
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    return [exe, *cmd[1:]]


def toolchain_available() -> bool:
    return bool(
        _enabled()
        and shutil.which("git")
        and shutil.which("node")
        and (shutil.which("pnpm") or shutil.which("npm") or shutil.which("yarn"))
    )


def _run(cmd: list[str], cwd: str | None, timeout: int) -> tuple[int, str]:
    resolved = _resolve(cmd)
    if not resolved:
        return -1, f"command not found: {cmd[0]}"
    try:
        proc = subprocess.run(
            resolved,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, f"{proc.stdout}\n{proc.stderr}".strip()
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


def _package_manager(repo_dir: str) -> tuple[list[str], list[str]]:
    """(install_cmd, exec_prefix) chosen by the repo's lockfile."""
    if os.path.exists(os.path.join(repo_dir, "pnpm-lock.yaml")):
        return ["pnpm", "install", "--frozen-lockfile", "--prefer-offline"], ["pnpm", "exec"]
    if os.path.exists(os.path.join(repo_dir, "yarn.lock")):
        return ["yarn", "install", "--frozen-lockfile"], ["yarn", "run"]
    return ["npm", "install", "--no-audit", "--no-fund"], ["npx", "--no-install"]


def _clone(client, branch: str, dest: str) -> bool:
    # Token in the URL — never log this command's argv.
    url = f"https://x-access-token:{client._token}@github.com/{client.repo}.git"
    resolved = _resolve(["git", "clone", "--depth", "1", "--branch", branch, url, dest])
    if not resolved:
        return False
    try:
        proc = subprocess.run(resolved, capture_output=True, text=True, timeout=CLONE_TIMEOUT, check=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sandbox clone error: %s", exc)
        return False
    if proc.returncode != 0:
        logger.warning("sandbox clone failed (rc=%s)", proc.returncode)  # no stderr — may echo URL
        return False
    return True


def _apply(edits: list[FileEdit], repo_dir: str) -> None:
    for e in edits:
        target = os.path.join(repo_dir, e.path)
        os.makedirs(os.path.dirname(target) or repo_dir, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(e.new_content)


def _typecheck(repo_dir: str, exec_prefix: list[str]) -> tuple[bool, str]:
    """Run `tsc --noEmit`. Treated as clean if the repo has no tsconfig."""
    if not os.path.exists(os.path.join(repo_dir, "tsconfig.json")):
        return True, ""
    code, out = _run([*exec_prefix, "tsc", "--noEmit"], repo_dir, CHECK_TIMEOUT)
    return code == 0, out


def verify_and_repair(client, profile: dict, run, result: FixResult, finding_codes: list[str]):
    """Type-check the edits in a sandbox; repair on failure. Returns (FixResult, note).

    On unrecoverable type errors the result's edits are cleared (so the caller
    fails the job rather than opening a PR that won't build).
    """
    if not result.edits:
        return result, ""
    if not toolchain_available():
        logger.info("sandbox toolchain unavailable; skipping build verification")
        return result, "_Build verification skipped (no Node toolchain on this host)._"

    branch = profile.get("default_branch") or "main"
    tmp = tempfile.mkdtemp(prefix="signalor-fix-")
    try:
        if not _clone(client, branch, tmp):
            return result, "_Build verification skipped (repo clone failed)._"

        install_cmd, exec_prefix = _package_manager(tmp)
        code, out = _run(install_cmd, tmp, INSTALL_TIMEOUT)
        if code != 0:
            logger.warning("sandbox install failed: %s", out[-400:])
            return result, "_Build verification skipped (dependency install failed)._"

        # If the repo has a tsconfig but tsc can't actually run, skip rather than
        # mistake an infra failure for type errors and throw away good edits.
        if os.path.exists(os.path.join(tmp, "tsconfig.json")):
            rc, _ = _run([*exec_prefix, "tsc", "--version"], tmp, 60)
            if rc != 0:
                return result, "_Build verification skipped (type-checker unavailable in repo)._"

        edits = result.edits
        errors = ""
        for attempt in range(MAX_REPAIRS + 1):
            _apply(edits, tmp)
            ok, errors = _typecheck(tmp, exec_prefix)
            if ok:
                result.edits = edits
                note = "Type-checked clean in a sandbox build before opening this PR."
                if attempt:
                    note += f" (auto-repaired {attempt}x)"
                return result, note
            if attempt == MAX_REPAIRS:
                break
            logger.info("sandbox typecheck failed (attempt %s); repairing", attempt + 1)
            rep = fix_agent.repair_edits(finding_codes, edits, errors, client, profile)
            if not (rep["result"] and rep["result"].edits):
                break
            edits = rep["result"].edits

        # Never compiled — don't open a broken PR.
        result.edits = []
        return result, (
            f"Could not produce code that passes the project's type-check after {MAX_REPAIRS} "
            f"repair attempts. Last errors:\n```\n{errors[:MAX_ERROR_CHARS]}\n```"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
