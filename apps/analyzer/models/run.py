"""The analysis run and its immediate scoring artefacts."""

import secrets

from django.db import models


def _generate_slug():
    return secrets.token_urlsafe(8)


class AnalysisRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        CRAWLING = "crawling"
        ANALYZING = "analyzing"
        SCORING = "scoring"
        COMPLETE = "complete"
        FAILED = "failed"

    class RunType(models.TextChoices):
        SINGLE_PAGE = "single_page"
        FULL_SITE = "full_site"

    slug = models.CharField(max_length=20, unique=True, blank=True, default="")
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="analysis_runs",
        null=True,
        blank=True,
    )
    url = models.URLField(max_length=2048)
    brand_name = models.CharField(max_length=255, blank=True, default="")
    country = models.CharField(max_length=100, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    run_type = models.CharField(max_length=20, choices=RunType.choices, default=RunType.SINGLE_PAGE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    progress = models.IntegerField(default=0)
    # Human-readable description of the work in flight, shown on the analysing
    # screen. ``status`` only distinguishes crawling/analyzing/scoring, which is
    # far too coarse: two checkpoints (prompt firing and competitor discovery)
    # account for most of the wall-clock, so a user watching those sees a frozen
    # bar and no explanation. This says what is actually happening right now.
    phase = models.CharField(max_length=140, blank=True, default="")
    composite_score = models.FloatField(null=True, blank=True)
    content_hash = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    # User-selected prompts from verified onboarding / post-checkout launch (empty for other flows)
    onboarding_prompts = models.JSONField(default=list, blank=True)
    storefront_password = models.CharField(max_length=255, blank=True, default="")
    llm_logs = models.JSONField(default=list, blank=True)
    # Total USD this run spent on LLM calls, summed from the exact per-call charge
    # OpenRouter returns. Denormalized out of ``llm_logs`` on purpose: that field
    # is a ~128 KB JSON blob, so aggregating spend per customer or per month by
    # parsing it is not viable. Indexed because every budget check filters on it.
    llm_cost_usd = models.FloatField(default=0.0, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["status"]),
            models.Index(fields=["slug"]),
            models.Index(fields=["email", "status"]),
            # Dashboard run-list: filter(organization_id=...).order_by("-created_at").
            # The FK index alone can't satisfy the sort; this composite does.
            models.Index(fields=["organization", "-created_at"]),
            # Email-scoped run-list (AnalysisRunListView): filter(email=...)
            # .order_by("-created_at"). Mirrors the organization composite above.
            models.Index(fields=["email", "-created_at"], name="idx_run_email_created"),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            while True:
                candidate = _generate_slug()
                if not AnalysisRun.objects.filter(slug=candidate).exists():
                    self.slug = candidate
                    break
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Run #{self.pk} [{self.slug}] - {self.url} ({self.status})"


class PageScore(models.Model):
    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="page_scores")
    url = models.URLField(max_length=2048)
    content_score = models.FloatField(default=0)
    content_details = models.JSONField(default=dict)
    schema_score = models.FloatField(default=0)
    schema_details = models.JSONField(default=dict)
    eeat_score = models.FloatField(default=0)
    eeat_details = models.JSONField(default=dict)
    technical_score = models.FloatField(default=0)
    technical_details = models.JSONField(default=dict)
    entity_score = models.FloatField(default=0)
    entity_details = models.JSONField(default=dict)
    ai_visibility_score = models.FloatField(default=0)
    ai_visibility_details = models.JSONField(default=dict)
    composite_score = models.FloatField(default=0)
    content_hash = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-composite_score"]

    def __str__(self):
        return f"PageScore {self.url} — {self.composite_score:.1f}"


class AIVisibilityProbe(models.Model):
    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="ai_probes")
    prompt_used = models.TextField()
    llm_response = models.TextField(blank=True, default="")
    brand_mentioned = models.BooleanField(default=False)
    confidence = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Probe: {'✓' if self.brand_mentioned else '✗'} — {self.prompt_used[:60]}"


class BrandVisibility(models.Model):
    analysis_run = models.OneToOneField(
        AnalysisRun, on_delete=models.CASCADE, related_name="brand_visibility"
    )
    google_score = models.FloatField(default=0)
    google_details = models.JSONField(default=dict)
    reddit_score = models.FloatField(default=0)
    reddit_details = models.JSONField(default=dict)
    web_mentions_score = models.FloatField(default=0)
    web_mentions_details = models.JSONField(default=dict)
    social_presence_details = models.JSONField(
        default=dict,
        blank=True,
        help_text="Instagram/Facebook public metrics and derived presence scores",
    )
    ai_brand_facts = models.JSONField(
        default=dict,
        blank=True,
        help_text="LLM-grounded notes on how AI may reflect the brand from visibility signals",
    )
    overall_score = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"BrandVisibility Run#{self.analysis_run_id} — {self.overall_score:.1f}"


class ScheduledAnalysis(models.Model):
    class Frequency(models.TextChoices):
        ONCE = "once"
        WEEKLY = "weekly"
        MONTHLY = "monthly"

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="scheduled_analyses",
    )
    email = models.EmailField(db_index=True)
    url = models.URLField(max_length=2048)
    brand_name = models.CharField(max_length=255, blank=True, default="")
    frequency = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.WEEKLY)
    next_run_at = models.DateTimeField()
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run_slug = models.CharField(max_length=20, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("organization", "email")]
        indexes = [
            models.Index(fields=["next_run_at", "is_active"]),
        ]

    def __str__(self):
        return f"Schedule<{self.email} {self.frequency}>"


class AgentLogEntry(models.Model):
    """Skeleton for future ingestion of AI crawler hits (Cloudflare Logpush /
    Vercel Edge logs). No ingestion in v1 — the table exists so the frontend
    Agent-log tab can query an empty but well-typed endpoint."""

    class Source(models.TextChoices):
        CLOUDFLARE = "cloudflare"
        VERCEL = "vercel"
        MANUAL = "manual"

    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="agent_log_entries")
    bot_name = models.CharField(max_length=64, db_index=True)
    path = models.CharField(max_length=2048)
    status_code = models.IntegerField(default=0)
    ts = models.DateTimeField(db_index=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-ts"]
        indexes = [models.Index(fields=["analysis_run", "bot_name"])]

    def __str__(self):
        return f"AgentLogEntry<{self.bot_name} {self.path}>"

