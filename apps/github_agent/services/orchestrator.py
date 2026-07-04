"""
Open-a-fix-PR orchestration: profile the repo, generate edits, commit them on a
branch, open the PR, and record everything on the GithubFixJob.

The job row is the agent's memory of this action — status, PR number, files
changed, and the before-score for the post-merge verification loop.
"""

import logging
import re

from django.utils import timezone

from apps.analyzer.models import Recommendation

from ..models import GithubFixJob
from . import agent as fix_agent
from . import fixers, sandbox
from .client import GithubClient
from .repo_profile import detect_profile

logger = logging.getLogger("apps")

# Re-profile the repo if the cached profile is older than this.
_PROFILE_TTL = timezone.timedelta(hours=12)

_BRANCH_PREFIX = "signalor/geo-fix-"


def _ensure_profile(installation, client) -> dict:
    profile = installation.repo_profile or {}
    fresh = (
        profile
        and installation.repo_profile_updated_at
        and installation.repo_profile_updated_at > timezone.now() - _PROFILE_TTL
    )
    if not fresh:
        profile = detect_profile(client)
        installation.repo_profile = profile
        installation.repo_profile_updated_at = timezone.now()
        installation.default_branch = profile.get("default_branch", installation.default_branch)
        installation.save(
            update_fields=["repo_profile", "repo_profile_updated_at", "default_branch", "updated_at"]
        )
    return profile


def _collect_edits(client, profile: dict, run, finding_codes: list[str]):
    """Route each finding to a deterministic fixer or the AI agent, combine the edits.

    Returns (FixResult, reasoning_text). Edits are de-duplicated by path (first wins)
    so two findings touching the same file don't double-commit with a stale sha.
    """
    det = [c for c in finding_codes if c in fixers.SUPPORTED_FINDINGS]
    agent_codes = [c for c in finding_codes if c not in fixers.SUPPORTED_FINDINGS]

    result = fixers.build_edits(client, profile, run, det) if det else fixers.FixResult()
    reasoning: list[str] = []

    for code in agent_codes:
        rec = Recommendation.objects.filter(analysis_run=run, finding_code=code).first()
        finding = {
            "finding_code": code,
            "pillar": getattr(rec, "pillar", ""),
            "title": getattr(rec, "title", "") or code,
            "description": getattr(rec, "description", ""),
            "action": getattr(rec, "action", ""),
        }
        ar = fix_agent.generate_edits(finding, client, profile, run)
        if ar["result"] and ar["result"].edits:
            result.edits.extend(ar["result"].edits)
            result.applied.extend(ar["result"].applied)
            if ar["reasoning"]:
                reasoning.append(f"**{code}**\n{ar['reasoning']}")
        else:
            result.skipped.append(code)
            if ar.get("cannot_fix"):
                reasoning.append(f"**{code}** — could not fix: {ar['cannot_fix']}")

    seen: set[str] = set()
    deduped = []
    for e in result.edits:
        if e.path in seen:
            continue
        seen.add(e.path)
        deduped.append(e)
    result.edits = deduped
    return result, "\n\n".join(reasoning)


def _clean_fail_message(reasoning: str) -> str:
    """Turn the markdown-wrapped agent reasoning into a plain, user-facing reason.

    Entries look like ``**no_statistics** — could not fix: <reason>``; strip the
    code prefix and bold markers so the UI shows just the explanation.
    """
    text = (reasoning or "").strip()
    text = re.sub(r"\*\*[^*]+\*\*\s*[—–-]\s*could not fix:\s*", "", text, flags=re.IGNORECASE)
    return text.replace("**", "").strip()


def _pr_body(run, applied: list[str], skipped: list[str], edits, reasoning: str = "") -> str:
    lines = [
        "## 🤖 Signalor GEO auto-fix",
        "",
        f"These changes raise the GEO/AI-visibility score for **{run.url}**.",
        "Review and merge — Signalor re-checks the score after merge.",
        "",
        "### Changes",
    ]
    for e in edits:
        lines.append(f"- `{e.path}` — {e.summary}")
    if applied:
        lines += ["", "### Findings addressed"]
        for code in applied:
            lines.append(f"- `{code}` — {fixers.SUPPORTED_FINDINGS.get(code, code)}")
    if reasoning:
        lines += ["", "### How", reasoning[:4000]]
    if skipped:
        lines += [
            "",
            "### Skipped (already present or not applicable)",
            ", ".join(f"`{c}`" for c in skipped),
        ]
    lines += ["", "---", "_Opened by the Signalor GitHub App._"]
    return "\n".join(lines)


def open_fix_pr(job_id: int) -> None:
    """Run a fix job end to end. Safe to call from a background thread."""
    try:
        job = GithubFixJob.objects.select_related("installation", "analysis_run").get(pk=job_id)
    except GithubFixJob.DoesNotExist:
        logger.error("FixJob %s not found", job_id)
        return

    installation, run = job.installation, job.analysis_run
    job.status = GithubFixJob.Status.RUNNING
    job.score_before = run.composite_score
    job.save(update_fields=["status", "score_before", "updated_at"])

    try:
        if not installation.repo_full_name:
            raise ValueError("Installation has no repo selected")

        client = GithubClient(installation.installation_id, installation.repo_full_name)
        profile = _ensure_profile(installation, client)
        result, reasoning = _collect_edits(client, profile, run, job.finding_codes)

        # Verify the edits actually compile (sandbox type-check + self-repair) before
        # opening the PR; clears edits if they never build so we fail instead of
        # opening a broken PR. No-op when the host has no Node toolchain.
        result, verify_note = sandbox.verify_and_repair(client, profile, run, result, job.finding_codes)
        if verify_note:
            reasoning = f"{reasoning}\n\n{verify_note}".strip() if reasoning else verify_note
        job.reasoning = reasoning[:8000]

        if not result.edits:
            job.status = GithubFixJob.Status.FAILED
            job.error_message = (
                _clean_fail_message(reasoning)[:1000]
                or "No applicable code changes — the targeted fixes are already present "
                "or don't apply to this repo."
            )
            job.files_changed = []
            job.save(update_fields=["status", "reasoning", "error_message", "files_changed", "updated_at"])
            logger.info("FixJob %s produced no edits (skipped=%s)", job_id, result.skipped)
            return

        branch = f"{_BRANCH_PREFIX}{job.id}"
        base = profile.get("default_branch") or installation.default_branch or "main"
        base_sha = client.get_branch_sha(base)
        client.create_branch(branch, base_sha)

        for edit in result.edits:
            client.put_file(
                edit.path,
                edit.new_content,
                message=f"signalor: {edit.summary}",
                branch=branch,
                sha=edit.sha,
            )

        title = "Signalor: GEO fixes (" + ", ".join(result.applied) + ")"
        pr = client.create_pull_request(
            title=title[:250],
            head=branch,
            base=base,
            body=_pr_body(run, result.applied, result.skipped, result.edits, reasoning),
        )

        job.branch_name = branch
        job.pr_number = pr.get("number")
        job.pr_url = pr.get("html_url", "")
        job.files_changed = [{"path": e.path, "summary": e.summary} for e in result.edits]
        job.finding_codes = result.applied or job.finding_codes
        job.status = GithubFixJob.Status.OPEN
        job.save(
            update_fields=[
                "branch_name",
                "pr_number",
                "pr_url",
                "files_changed",
                "finding_codes",
                "reasoning",
                "status",
                "updated_at",
            ]
        )
        logger.info("FixJob %s opened PR #%s on %s", job_id, job.pr_number, installation.repo_full_name)

    except Exception as exc:  # noqa: BLE001
        logger.error("FixJob %s failed: %s", job_id, exc)
        job.status = GithubFixJob.Status.FAILED
        job.error_message = str(exc)[:1000]
        job.save(update_fields=["status", "error_message", "updated_at"])
