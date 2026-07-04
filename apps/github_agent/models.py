"""
Models for the GitHub Agent — the autonomous GEO fixer.

The agent's "memory" lives here, not in the LLM: an installation records which
repo we're connected to (keyed back to an AnalysisRun + Organization), and every
fix attempt is a GithubFixJob row (one per PR). On each action the orchestrator
re-assembles context from these rows + the analyzer's Recommendation rows, so
nothing depends on model memory and a restart loses nothing.
"""

from django.db import models


class GithubInstallation(models.Model):
    """A GitHub App installation on a customer's repo.

    We never store a long-lived token — only the ``installation_id``. Short-lived
    (~1h) installation access tokens are minted on demand from the App's private
    key (see ``services/auth.py``).
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="github_installations",
        null=True,
        blank=True,
    )
    # Stable GitHub App install id (the value GitHub sends to the callback).
    installation_id = models.BigIntegerField(unique=True)
    account_login = models.CharField(max_length=255, blank=True, default="")
    account_type = models.CharField(max_length=20, blank=True, default="")  # User | Organization
    # AnalysisRun.slug the install was started from — fallback link when a run
    # has no Organization yet, so status/fix lookups still resolve.
    connect_slug = models.CharField(max_length=20, blank=True, default="")

    # The repo we open PRs against. An install can grant several repos; v1 targets one.
    repo_full_name = models.CharField(max_length=255, blank=True, default="")  # "owner/name"
    repositories = models.JSONField(default=list, blank=True)  # ["owner/name", ...]
    default_branch = models.CharField(max_length=255, blank=True, default="main")

    # Cached framework + key paths so fixers know where code goes (see repo_profile.py).
    repo_profile = models.JSONField(default=dict, blank=True)
    repo_profile_updated_at = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["installation_id"]),
            models.Index(fields=["organization", "is_active"]),
            models.Index(fields=["connect_slug"]),
        ]

    def __str__(self):
        return f"GitHub install {self.installation_id} ({self.repo_full_name or self.account_login})"


class GithubFixJob(models.Model):
    """One auto-fix attempt → one Pull Request.

    Doubles as the dedup record: before opening a PR for a finding we check there
    isn't already an open/running job covering it, so the agent never spams
    duplicate PRs for the same finding.
    """

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        OPEN = "open"  # PR opened, awaiting human merge
        MERGED = "merged"
        CLOSED = "closed"  # PR closed without merge
        FAILED = "failed"

    installation = models.ForeignKey(
        GithubInstallation,
        on_delete=models.CASCADE,
        related_name="fix_jobs",
    )
    analysis_run = models.ForeignKey(
        "analyzer.AnalysisRun",
        on_delete=models.CASCADE,
        related_name="github_fix_jobs",
    )

    # Finding codes (from analyzer recommendations) this PR addresses.
    finding_codes = models.JSONField(default=list, blank=True)

    branch_name = models.CharField(max_length=255, blank=True, default="")
    pr_number = models.IntegerField(null=True, blank=True)
    pr_url = models.URLField(max_length=1024, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # [{"path": "public/llms.txt", "summary": "Created llms.txt"}, ...]
    files_changed = models.JSONField(default=list, blank=True)
    error_message = models.TextField(blank=True, default="")
    # AI agent's plan/explanation for agent-generated fixes (shown in the PR body).
    reasoning = models.TextField(blank=True, default="")

    # Composite score before the fix and after the PR merges (verification loop).
    score_before = models.FloatField(null=True, blank=True)
    score_after = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["analysis_run", "-created_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["pr_number"]),
        ]

    def __str__(self):
        return f"FixJob #{self.pk} [{self.status}] PR #{self.pr_number or '-'}"
