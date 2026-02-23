from django.db import models


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

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="analysis_runs",
        null=True,
        blank=True,
    )
    url = models.URLField(max_length=2048)
    brand_name = models.CharField(max_length=255, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    run_type = models.CharField(
        max_length=20, choices=RunType.choices, default=RunType.SINGLE_PAGE
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    progress = models.IntegerField(default=0)
    composite_score = models.FloatField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    llm_logs = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Run #{self.pk} - {self.url} ({self.status})"


class PageScore(models.Model):
    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.CASCADE, related_name="page_scores"
    )
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-composite_score"]

    def __str__(self):
        return f"PageScore {self.url} — {self.composite_score:.1f}"


class Competitor(models.Model):
    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.CASCADE, related_name="competitors"
    )
    name = models.CharField(max_length=255)
    url = models.URLField(max_length=2048)
    industry = models.CharField(max_length=255, blank=True, default="")
    composite_score = models.FloatField(null=True, blank=True)
    scored = models.BooleanField(default=False)
    page_score = models.OneToOneField(
        PageScore, on_delete=models.SET_NULL, null=True, blank=True, related_name="competitor"
    )

    def __str__(self):
        return f"{self.name} ({self.url})"


class AIVisibilityProbe(models.Model):
    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.CASCADE, related_name="ai_probes"
    )
    prompt_used = models.TextField()
    llm_response = models.TextField(blank=True, default="")
    brand_mentioned = models.BooleanField(default=False)
    confidence = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Probe: {'✓' if self.brand_mentioned else '✗'} — {self.prompt_used[:60]}"


class Recommendation(models.Model):
    class Priority(models.TextChoices):
        CRITICAL = "critical"
        HIGH = "high"
        MEDIUM = "medium"
        LOW = "low"

    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.CASCADE, related_name="recommendations"
    )
    pillar = models.CharField(max_length=30)
    priority = models.CharField(max_length=10, choices=Priority.choices)
    title = models.CharField(max_length=255)
    description = models.TextField()
    action = models.TextField()
    impact_estimate = models.CharField(max_length=100, blank=True, default="")
    category = models.CharField(max_length=30)

    class Meta:
        ordering = ["priority", "pillar"]

    def __str__(self):
        return f"[{self.priority}] {self.title}"


class BrandVisibility(models.Model):
    analysis_run = models.OneToOneField(
        AnalysisRun, on_delete=models.CASCADE, related_name="brand_visibility"
    )
    google_score = models.FloatField(default=0)
    google_details = models.JSONField(default=dict)
    reddit_score = models.FloatField(default=0)
    reddit_details = models.JSONField(default=dict)
    medium_score = models.FloatField(default=0)
    medium_details = models.JSONField(default=dict)
    web_mentions_score = models.FloatField(default=0)
    web_mentions_details = models.JSONField(default=dict)
    overall_score = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"BrandVisibility Run#{self.analysis_run_id} — {self.overall_score:.1f}"
