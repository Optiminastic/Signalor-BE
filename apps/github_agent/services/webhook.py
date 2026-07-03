"""
GitHub webhook handling: verify the signature, then react to the events we
subscribe to (pull_request, installation). On merge we flip the job to MERGED
and kick off a re-crawl so the score-after can be recorded.
"""

import hashlib
import hmac
import logging

from django.conf import settings

from ..models import GithubFixJob, GithubInstallation

logger = logging.getLogger("apps")


def verify_signature(body: bytes, signature_header: str) -> bool:
    """Constant-time HMAC-SHA256 check against GITHUB_WEBHOOK_SECRET."""
    secret = settings.GITHUB_WEBHOOK_SECRET or ""
    if not secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def handle_event(event: str, payload: dict) -> None:
    """Dispatch a verified webhook payload."""
    if event == "pull_request":
        _handle_pull_request(payload)
    elif event == "installation":
        _handle_installation(payload)
    # other events ignored for v1


def _handle_pull_request(payload: dict) -> None:
    action = payload.get("action")
    pr = payload.get("pull_request") or {}
    number = pr.get("number")
    if number is None:
        return

    job = (
        GithubFixJob.objects.filter(pr_number=number)
        .select_related("analysis_run", "installation")
        .order_by("-created_at")
        .first()
    )
    if not job:
        return

    if action == "closed":
        if pr.get("merged"):
            job.status = GithubFixJob.Status.MERGED
            job.save(update_fields=["status", "updated_at"])
            _trigger_recrawl(job)
        else:
            job.status = GithubFixJob.Status.CLOSED
            job.save(update_fields=["status", "updated_at"])


def _handle_installation(payload: dict) -> None:
    action = payload.get("action")
    install = payload.get("installation") or {}
    install_id = install.get("id")
    if install_id is None:
        return
    if action in ("deleted", "suspend"):
        GithubInstallation.objects.filter(installation_id=install_id).update(is_active=False)
    elif action == "unsuspend":
        GithubInstallation.objects.filter(installation_id=install_id).update(is_active=True)


def _trigger_recrawl(job: GithubFixJob) -> None:
    """Re-run the analyzer for this site after a merge, then record the fix's SEO
    impact on the job (``score_after`` = the fresh composite score).

    Runs in its own thread so the webhook returns immediately; best-effort
    throughout — a missing/renamed analyzer entrypoint never breaks the webhook.
    """
    import threading  # noqa: PLC0415

    job_id = job.id
    run_id = job.analysis_run_id
    pr_number = job.pr_number

    def _work() -> None:
        from django.db import close_old_connections  # noqa: PLC0415

        try:
            from apps.analyzer.tasks import run_single_page_analysis  # noqa: PLC0415

            close_old_connections()
            # Run synchronously here (not start_analysis_task, which detaches a
            # thread) so we know when it finishes and can read the new score.
            run_single_page_analysis(run_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Re-crawl failed for job %s: %s", job_id, exc)
            return

        try:
            from apps.analyzer.models import AnalysisRun  # noqa: PLC0415

            close_old_connections()
            run = AnalysisRun.objects.filter(pk=run_id).first()
            if run and run.composite_score is not None:
                GithubFixJob.objects.filter(pk=job_id).update(score_after=run.composite_score)
                logger.info(
                    "Recorded score_after=%s for job %s (PR #%s)",
                    run.composite_score,
                    job_id,
                    pr_number,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not record score_after for job %s: %s", job_id, exc)

    threading.Thread(target=_work, daemon=True).start()
