"""
Open-a-fix-PR orchestration: profile the repo, generate edits, commit them on a
branch, open the PR, and record everything on the GithubFixJob.

The job row is the agent's memory of this action — status, PR number, files
changed, and the before-score for the post-merge verification loop.
"""

import logging
import re

from django.conf import settings
from django.utils import timezone

from apps.analyzer.models import Recommendation
from apps.integrations.github.client import GithubClient
from apps.integrations.github.repo_profile import detect_profile
from apps.remediation.services import agent as fix_agent
from apps.remediation.services import fixers, sandbox

from ..models import GithubFixJob
from . import pr_format

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


def _collect_content_edits(client, profile: dict, run, content_edits: list[dict]):
    """Apply user-supplied Content-Optimisation edits via the agent (verbatim
    original→new text). Returns (FixResult, reasoning_text)."""
    ar = fix_agent.generate_content_edits(content_edits, client, profile, run)
    result = ar.get("result") or fixers.FixResult()
    reasoning = ar.get("reasoning") or ""
    if not result.edits and ar.get("cannot_fix"):
        reasoning = f"Could not apply the content edit: {ar['cannot_fix']}"

    seen: set[str] = set()
    deduped = []
    for e in result.edits:
        if e.path in seen:
            continue
        seen.add(e.path)
        deduped.append(e)
    result.edits = deduped
    return result, reasoning


def _clean_fail_message(reasoning: str) -> str:
    """Turn the markdown-wrapped agent reasoning into a plain, user-facing reason.

    Entries look like ``**no_statistics** — could not fix: <reason>``; strip the
    code prefix and bold markers so the UI shows just the explanation.
    """
    text = (reasoning or "").strip()
    text = re.sub(r"\*\*[^*]+\*\*\s*[—–-]\s*could not fix:\s*", "", text, flags=re.IGNORECASE)
    return text.replace("**", "").strip()


def _no_edit_outcome(proposed_any: bool, reasoning: str) -> tuple[str, str]:
    """Status + user-facing message for a run that produced no edits.

    The distinction matters to the UI. Nothing proposed means the agent
    **declined**: the fix needs real-world data it must not invent (customer
    quotes, statistics, case-study numbers), or there was nothing left to change.
    That is the no-fabrication guard working, and it is shown as "needs your
    input", never as an error.

    Edits that were proposed and then cleared mean the sandbox could not get them
    to build — a genuine failure worth flagging in red.
    """
    declined = not proposed_any
    status = GithubFixJob.Status.DECLINED if declined else GithubFixJob.Status.FAILED
    fallback = (
        "Nothing to change — the targeted fixes are already present or don't apply to this repo."
        if declined
        else "The proposed changes did not build, so no pull request was opened."
    )
    return status, (_clean_fail_message(reasoning)[:1000] or fallback)


def _fix_context(run, codes: list[str], is_content: bool) -> pr_format.FixContext:
    """Resolve the human framing for the PR: which task, which pillar, what to call it.

    Everything here is best-effort. A missing Recommendation or org slug costs us
    a nicer title and the backlink, never the fix itself.
    """
    from apps.analyzer.models import Recommendation, UserAction

    rec = (
        Recommendation.objects.filter(analysis_run=run, finding_code__in=codes).first()
        if codes
        else None
    )
    action = (
        UserAction.objects.filter(analysis_run=run, recommendation=rec).only("id", "title").first()
        if rec
        else None
    )
    org_slug = getattr(getattr(run, "organization", None), "slug", "") or ""
    task_url = ""
    if action and org_slug:
        base = settings.FRONTEND_BASE_URL.rstrip("/")
        task_url = f"{base}/dashboard/{org_slug}/tasks/{action.id}"

    return pr_format.FixContext(
        site_url=run.url,
        pillar=getattr(rec, "pillar", "") or "",
        headline=getattr(rec, "title", "") or (getattr(action, "title", "") or ""),
        finding_codes=codes,
        task_url=task_url,
        task_label=getattr(action, "title", "") or getattr(rec, "title", "") or "",
        is_content=is_content,
    )


# Label colours (GitHub wants hex without '#'). Brand red for ours, neutral for scope.
_LABEL_COLOURS = {"signalor": "e04a3d", "geo": "5b6470"}
_LABEL_FALLBACK_COLOUR = "8b949e"


def _apply_labels(client, pr_number: int, labels: list[str]) -> None:
    """Best-effort labelling — never let it fail a PR that is already open.

    Labelling needs the App's `issues: write` permission; an install granted
    before that was requested will 403 here. The fix is still delivered, so this
    logs and moves on rather than failing the job.
    """
    if not pr_number or not labels:
        return
    try:
        for name in labels:
            client.ensure_label(name, _LABEL_COLOURS.get(name, _LABEL_FALLBACK_COLOUR), "Signalor")
        client.add_labels(pr_number, labels)
    except Exception as exc:  # noqa: BLE001 - cosmetic step, must not break the fix
        logger.warning("Could not label PR #%s: %s", pr_number, exc)


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
            # Left empty on purpose when the install granted several repos and
            # none clearly matched the brand's domain. Failing here is correct:
            # guessing would open a PR on an unrelated repository.
            raise ValueError(
                "No repository selected for this brand. Choose which of the "
                "connected repositories SignalorAI should open PRs against."
            )

        client = GithubClient(installation.installation_id, installation.repo_full_name)
        profile = _ensure_profile(installation, client)
        is_content = bool(job.content_edits)
        if is_content:
            result, reasoning = _collect_content_edits(client, profile, run, job.content_edits)
        else:
            result, reasoning = _collect_edits(client, profile, run, job.finding_codes)

        # Whether the agent proposed anything at all, captured BEFORE the sandbox
        # runs. Nothing proposed means the agent declined; edits that existed and
        # were then cleared mean they never built, which is a real failure.
        proposed_any = bool(result.edits)

        # Verify the edits actually compile (sandbox type-check + self-repair) before
        # opening the PR; clears edits if they never build so we fail instead of
        # opening a broken PR. No-op when the host has no Node toolchain.
        result, verify_note = sandbox.verify_and_repair(client, profile, run, result, job.finding_codes)
        if verify_note:
            reasoning = f"{reasoning}\n\n{verify_note}".strip() if reasoning else verify_note
        job.reasoning = reasoning[:8000]

        if not result.edits:
            job.status, job.error_message = _no_edit_outcome(proposed_any, reasoning)
            job.files_changed = []
            job.save(update_fields=["status", "reasoning", "error_message", "files_changed", "updated_at"])
            logger.info(
                "FixJob %s produced no edits (status=%s, skipped=%s)",
                job_id,
                job.status,
                result.skipped,
            )
            return

        branch = f"{_BRANCH_PREFIX}{job.id}"
        base = profile.get("default_branch") or installation.default_branch or "main"
        base_sha = client.get_branch_sha(base)
        client.create_branch(branch, base_sha)

        ctx = _fix_context(run, result.applied or job.finding_codes, is_content)

        for edit in result.edits:
            client.put_file(
                edit.path,
                edit.new_content,
                message=pr_format.commit_message(ctx, edit.summary),
                branch=branch,
                sha=edit.sha,
            )

        changes = [(e.path, e.summary) for e in result.edits]
        pr = client.create_pull_request(
            title=pr_format.pr_title(ctx)[:250],
            head=branch,
            base=base,
            body=pr_format.pr_body(
                ctx,
                changes,
                result.skipped,
                reasoning,
                content_edits=job.content_edits if is_content else None,
            ),
        )
        _apply_labels(client, pr.get("number"), pr_format.labels_for(ctx))

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
