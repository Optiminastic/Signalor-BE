"""Technical crawl artefacts: crawler hits, sitemap and schema audits."""

from django.db import models

from .run import AnalysisRun


class CrawlerHit(models.Model):
    """One AI-crawler request against the brand's site, reported by the site's
    edge/log integration through the crawler ingest endpoint. Only requests
    whose user agent matches a known AI crawler are ever stored."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="crawler_hits"
    )
    bot = models.CharField(max_length=40)
    path = models.CharField(max_length=512, blank=True, default="/")
    user_agent = models.CharField(max_length=300, blank=True, default="")
    hit_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "hit_at"])]

    def __str__(self):
        return f"{self.bot} {self.path} @ {self.hit_at:%Y-%m-%d %H:%M}"


class GeoImprovement(models.Model):
    """Tracks an auto-applied GEO SEO improvement pushed to Shopify or WordPress."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPLIED = "applied", "Applied"
        FAILED = "failed", "Failed"

    class ImprovementType(models.TextChoices):
        META_TITLE = "meta_title", "Meta Title"
        META_DESCRIPTION = "meta_description", "Meta Description"
        HREFLANG = "hreflang", "Hreflang Tag"
        SCHEMA_MARKUP = "schema_markup", "Schema Markup"
        ALT_TEXT = "alt_text", "Image Alt Text"
        GEO_META = "geo_meta", "Geo Meta Tag"
        CONTENT_UPDATE = "content_update", "Content Update"

    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="geo_improvements",
    )
    provider = models.CharField(max_length=20)  # shopify | wordpress
    improvement_type = models.CharField(max_length=30, choices=ImprovementType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # What resource was updated (e.g. product ID, post ID, page ID)
    resource_type = models.CharField(max_length=50, blank=True, default="")
    resource_id = models.CharField(max_length=100, blank=True, default="")
    resource_title = models.CharField(max_length=500, blank=True, default="")

    # Before / after
    field_name = models.CharField(max_length=100, blank=True, default="")
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")

    # Score impact
    score_before = models.FloatField(null=True, blank=True)
    score_after = models.FloatField(null=True, blank=True)

    error_message = models.TextField(blank=True, default="")
    applied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["analysis_run", "status"]),
        ]

    def __str__(self):
        return f"GeoImprovement<{self.improvement_type} {self.status}>"


class SitemapAudit(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        COMPLETE = "complete"
        FAILED = "failed"

    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="sitemap_audits")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    progress = models.IntegerField(default=0)
    sitemap_url = models.URLField(max_length=2048, blank=True, default="")
    crawl_limit = models.IntegerField(default=200)

    total_urls = models.IntegerField(default=0)
    indexed_count = models.IntegerField(default=0)
    redirect_count = models.IntegerField(default=0)
    queued_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)

    avg_lcp_ms = models.IntegerField(null=True, blank=True)
    avg_fcp_ms = models.IntegerField(null=True, blank=True)
    avg_ttfb_ms = models.IntegerField(null=True, blank=True)
    avg_ai_score = models.IntegerField(null=True, blank=True)

    truncated = models.BooleanField(default=False)
    discovered_url_count = models.IntegerField(default=0)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"SitemapAudit<{self.analysis_run_id} {self.status} {self.progress}%>"


class SitemapAuditPage(models.Model):
    class State(models.TextChoices):
        CRAWLED = "crawled"
        REDIRECT = "redirect"
        QUEUED = "queued"
        FAILED = "failed"

    class Severity(models.TextChoices):
        OK = "ok"
        WARN = "warn"
        FAIL = "fail"

    audit = models.ForeignKey(SitemapAudit, on_delete=models.CASCADE, related_name="pages")
    url = models.URLField(max_length=2048)
    path = models.CharField(max_length=2048, blank=True, default="")
    final_url = models.URLField(max_length=2048, blank=True, default="")

    state = models.CharField(max_length=12, choices=State.choices, default=State.QUEUED, db_index=True)
    status_code = models.IntegerField(default=0, db_index=True)
    redirect_count = models.IntegerField(default=0)

    title = models.CharField(max_length=512, blank=True, default="")
    meta_description = models.CharField(max_length=1024, blank=True, default="")
    h1_count = models.IntegerField(default=0)

    word_count = models.IntegerField(default=0)
    text_ratio = models.FloatField(default=0.0)
    content_length = models.IntegerField(default=0)

    lcp_ms = models.IntegerField(null=True, blank=True)
    fcp_ms = models.IntegerField(null=True, blank=True)
    ttfb_ms = models.IntegerField(null=True, blank=True)
    server_ms = models.IntegerField(null=True, blank=True)

    resource_count = models.IntegerField(default=0)
    resource_bytes = models.IntegerField(default=0)

    link_count_total = models.IntegerField(default=0)
    link_count_internal = models.IntegerField(default=0)
    link_count_external = models.IntegerField(default=0)

    jsonld_count = models.IntegerField(default=0)
    has_canonical = models.BooleanField(default=False)
    has_og = models.BooleanField(default=False)
    is_noindex = models.BooleanField(default=False)

    robots_allows_gptbot = models.BooleanField(default=True)
    robots_allows_claudebot = models.BooleanField(default=True)
    robots_allows_perplexitybot = models.BooleanField(default=True)

    ai_score = models.IntegerField(default=0)
    severity = models.CharField(max_length=8, choices=Severity.choices, default=Severity.OK, db_index=True)
    findings = models.JSONField(default=list, blank=True)

    error_message = models.CharField(max_length=512, blank=True, default="")
    checked_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["audit_id", "id"]
        indexes = [
            models.Index(fields=["audit", "state"]),
            models.Index(fields=["audit", "severity"]),
        ]

    def __str__(self):
        return f"SitemapAuditPage<{self.url} {self.state} score={self.ai_score}>"


class SchemaWatch(models.Model):
    """A run of the Schema Watchtower — validates JSON-LD on a set of URLs
    (products, articles, FAQs) for breakage and drift. v1 is a static
    validator; v2 will diff against a stored baseline for drift detection."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        COMPLETE = "complete"
        FAILED = "failed"

    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="schema_watches")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    progress = models.IntegerField(default=0)

    total_urls = models.IntegerField(default=0)
    healthy_count = models.IntegerField(default=0)
    warn_count = models.IntegerField(default=0)
    broken_count = models.IntegerField(default=0)

    discovered_from_sitemap = models.BooleanField(default=True)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"SchemaWatch<{self.analysis_run_id} {self.status} {self.progress}%>"


class SchemaWatchPage(models.Model):
    """One URL snapshot inside a SchemaWatch run."""

    class Severity(models.TextChoices):
        OK = "ok"
        WARN = "warn"
        FAIL = "fail"

    watch = models.ForeignKey(SchemaWatch, on_delete=models.CASCADE, related_name="pages")
    url = models.URLField(max_length=2048)
    path = models.CharField(max_length=2048, blank=True, default="")
    page_kind = models.CharField(max_length=32, blank=True, default="")
    status_code = models.IntegerField(default=0)

    schema_types = models.JSONField(default=list, blank=True)
    jsonld_count = models.IntegerField(default=0)
    raw_jsonld = models.JSONField(default=list, blank=True)

    severity = models.CharField(max_length=8, choices=Severity.choices, default=Severity.OK, db_index=True)
    issues = models.JSONField(default=list, blank=True)
    fix_targets = models.JSONField(default=list, blank=True)

    error_message = models.CharField(max_length=512, blank=True, default="")
    checked_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["watch_id", "id"]
        indexes = [
            models.Index(fields=["watch", "severity"]),
        ]

    def __str__(self):
        return f"SchemaWatchPage<{self.url} {self.severity}>"

