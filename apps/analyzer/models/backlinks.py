"""Backlinks, the satellite blog network and its scheduling."""

from datetime import time

from django.db import models

from .prompts import PromptTrack
from .run import AnalysisRun


class BacklinkSnapshot(models.Model):
    """
    Cached domain-level backlink metrics fetched from DataForSEO.

    Reused across runs/prompts; refreshed when older than 7 days. Keyed by
    bare domain (no scheme, no path, no www. prefix) lowercased.
    """

    domain = models.CharField(max_length=255, unique=True, db_index=True)
    referring_domains = models.IntegerField(default=0)
    backlinks = models.IntegerField(default=0)
    rank = models.IntegerField(default=0)
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-referring_domains"]

    def __str__(self):
        return f"BacklinkSnapshot {self.domain} (RD={self.referring_domains})"


class BacklinkOpportunity(models.Model):
    """
    A site where the user can submit/list/earn a backlink — generated per prompt
    by the LLM based on the prompt's intent + the brand's profile.

    Drives the per-prompt "Backlink Opportunities" actions panel: each row is
    something the user can act on (submit a listing, request a review, post in
    a community, claim a profile).
    """

    class Category(models.TextChoices):
        DIRECTORY = "directory", "Directory"
        REVIEW = "review", "Review Site"
        PRESS = "press", "Press / Media"
        FORUM = "forum", "Community / Forum"
        RESOURCE = "resource", "Resource Page"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        SUGGESTED = "suggested", "Suggested"
        SUBMITTED = "submitted", "Submitted"
        LIVE = "live", "Live"
        DISMISSED = "dismissed", "Dismissed"

    prompt_track = models.ForeignKey(
        PromptTrack,
        on_delete=models.CASCADE,
        related_name="backlink_opportunities",
    )
    name = models.CharField(max_length=200)
    description = models.CharField(max_length=400, blank=True, default="")
    rationale = models.CharField(max_length=400, blank=True, default="")
    submit_url = models.URLField(max_length=2048)
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.DIRECTORY,
    )
    # Lower number = higher priority. 1=high, 2=medium, 3=low.
    priority = models.IntegerField(default=2)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SUGGESTED,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    live_url = models.URLField(max_length=2048, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "name"]
        indexes = [
            models.Index(fields=["prompt_track", "status"]),
        ]

    def __str__(self):
        return f"BacklinkOpportunity {self.name} ({self.status})"


class BlogAutomationConfig(models.Model):
    class PublishMode(models.TextChoices):
        AUTO_PUBLISH = "auto_publish", "Auto Publish"
        REVIEW_BEFORE_PUBLISH = "review_before_publish", "Review Before Publish"

    class PublishProvider(models.TextChoices):
        WORDPRESS = "wordpress", "WordPress"
        SHOPIFY = "shopify", "Shopify"
        NONE = "none", "None"

    user_email = models.EmailField(db_index=True)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="blog_automation_configs",
    )
    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="blog_automation_configs",
    )

    site_url = models.URLField(max_length=2048)
    topic = models.CharField(max_length=255, default="AI search visibility strategy")
    keywords = models.JSONField(default=list, blank=True)

    frequency_per_day = models.PositiveSmallIntegerField(default=1)
    publish_time = models.TimeField(default=time(hour=9, minute=0))
    mode = models.CharField(
        max_length=30,
        choices=PublishMode.choices,
        default=PublishMode.REVIEW_BEFORE_PUBLISH,
    )
    publish_provider = models.CharField(
        max_length=20,
        choices=PublishProvider.choices,
        default=PublishProvider.NONE,
    )
    is_active = models.BooleanField(default=True)
    last_queued_for = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user_email", "site_url"],
                name="unique_blog_config_per_user_site",
            )
        ]
        indexes = [
            models.Index(fields=["user_email", "is_active"]),
        ]

    def __str__(self):
        return f"BlogConfig<{self.user_email} {self.site_url}>"


class BlogAutomationJob(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        DRAFT = "draft", "Draft"
        NEEDS_REVIEW = "needs_review", "Needs Review"
        PUBLISHED = "published", "Published"
        FAILED = "failed", "Failed"

    config = models.ForeignKey(
        BlogAutomationConfig,
        on_delete=models.CASCADE,
        related_name="jobs",
    )
    user_email = models.EmailField(db_index=True)
    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="blog_automation_jobs",
    )

    scheduled_for = models.DateTimeField(db_index=True)
    provider = models.CharField(
        max_length=20,
        choices=BlogAutomationConfig.PublishProvider.choices,
        default=BlogAutomationConfig.PublishProvider.NONE,
    )
    mode = models.CharField(
        max_length=30,
        choices=BlogAutomationConfig.PublishMode.choices,
        default=BlogAutomationConfig.PublishMode.REVIEW_BEFORE_PUBLISH,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )

    topic = models.CharField(max_length=255, blank=True, default="")
    keywords = models.JSONField(default=list, blank=True)

    title = models.CharField(max_length=300, blank=True, default="")
    slug = models.CharField(max_length=120, blank=True, default="")
    meta_description = models.CharField(max_length=180, blank=True, default="")
    excerpt = models.TextField(blank=True, default="")
    content_markdown = models.TextField(blank=True, default="")
    tags = models.JSONField(default=list, blank=True)

    external_post_id = models.CharField(max_length=120, blank=True, default="")
    external_post_url = models.URLField(max_length=2048, blank=True, default="")
    published_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_for"]
        constraints = [
            models.UniqueConstraint(
                fields=["config", "scheduled_for"],
                name="unique_scheduled_slot_per_blog_config",
            )
        ]
        indexes = [
            models.Index(fields=["user_email", "status"]),
            models.Index(fields=["scheduled_for", "status"]),
        ]

    def __str__(self):
        return f"BlogJob<{self.user_email} {self.status} {self.scheduled_for}>"


class BacklinkSchedule(models.Model):
    """Per-brand daily auto-backlinks schedule.

    When active, the ``run_backlink_schedules`` cron command publishes one fresh
    blog to each of the 5 satellite sites (via ``services.backlink_engine``)
    every 24 hours, growing the brand's backlink footprint over time. Mirrors
    ``ScheduledAnalysis``: a per-row ``next_run_at`` the command filters on and
    bumps by one day after each run.
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="backlink_schedules",
        null=True,
        blank=True,
    )
    email = models.EmailField(db_index=True)
    run_slug = models.CharField(max_length=20, blank=True, default="")  # context run; refreshed to latest
    is_active = models.BooleanField(default=True)
    next_run_at = models.DateTimeField()
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_batch_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("organization", "email")]
        indexes = [
            models.Index(fields=["next_run_at", "is_active"]),
        ]

    def __str__(self):
        return f"BacklinkSchedule<{self.email} active={self.is_active}>"


class BacklinkProvider(models.Model):
    """Configured backlink-as-a-service vendor (one row per integration)."""

    slug = models.SlugField(max_length=40, unique=True)
    display_name = models.CharField(max_length=120)
    is_enabled = models.BooleanField(default=True)
    homepage_url = models.URLField(max_length=500, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name


class BacklinkProduct(models.Model):
    """
    A single buyable backlink listing — typically a guest post / niche edit
    on a specific domain. Catalog is refreshed periodically from the provider.
    """

    class LinkType(models.TextChoices):
        GUEST_POST = "guest_post", "Guest Post"
        NICHE_EDIT = "niche_edit", "Niche Edit"
        SPONSORED = "sponsored", "Sponsored Post"
        CITATION = "citation", "Citation / Listing"
        OTHER = "other", "Other"

    provider = models.ForeignKey(
        BacklinkProvider,
        on_delete=models.CASCADE,
        related_name="products",
    )
    sku = models.CharField(max_length=120, db_index=True)
    domain = models.CharField(max_length=255, db_index=True)
    title = models.CharField(max_length=300, blank=True, default="")
    link_type = models.CharField(max_length=20, choices=LinkType.choices, default=LinkType.GUEST_POST)
    domain_authority = models.IntegerField(null=True, blank=True)  # 0–100 scale
    domain_rank = models.IntegerField(null=True, blank=True)  # 0–1000 scale
    monthly_traffic = models.IntegerField(null=True, blank=True)
    niche_tags = models.JSONField(default=list, blank=True)
    language = models.CharField(max_length=8, default="en")
    country = models.CharField(max_length=8, blank=True, default="")
    do_follow = models.BooleanField(default=True)

    # All money in minor units (cents) to avoid float issues.
    wholesale_price_cents = models.IntegerField(default=0)
    retail_price_cents = models.IntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")
    lead_time_days = models.IntegerField(default=7)

    extras = models.JSONField(default=dict, blank=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-domain_authority", "domain"]
        constraints = [
            models.UniqueConstraint(fields=["provider", "sku"], name="uniq_provider_sku"),
        ]
        indexes = [
            models.Index(fields=["provider", "domain"]),
            models.Index(fields=["link_type"]),
        ]

    def __str__(self):
        return f"{self.domain} ({self.provider.slug})"


class BacklinkOrder(models.Model):
    """A placed order against a BacklinkProduct, tracked through fulfillment."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_PAYMENT = "pending_payment", "Pending payment"
        QUEUED = "queued", "Queued with provider"
        IN_PROGRESS = "in_progress", "In progress"
        DELIVERED = "delivered", "Delivered"
        REJECTED = "rejected", "Rejected"
        REFUNDED = "refunded", "Refunded"
        CANCELLED = "cancelled", "Cancelled"

    provider = models.ForeignKey(BacklinkProvider, on_delete=models.PROTECT, related_name="orders")
    product = models.ForeignKey(BacklinkProduct, on_delete=models.PROTECT, related_name="orders")
    user_email = models.EmailField()
    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="backlink_orders",
        null=True,
        blank=True,
    )
    prompt_track = models.ForeignKey(
        PromptTrack,
        on_delete=models.SET_NULL,
        related_name="backlink_orders",
        null=True,
        blank=True,
    )

    target_url = models.URLField(max_length=2048)
    anchor_text = models.CharField(max_length=300)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT, db_index=True)

    price_cents = models.IntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")

    provider_order_id = models.CharField(max_length=120, blank=True, default="")
    proof_url = models.URLField(max_length=2048, blank=True, default="")
    notes_for_provider = models.TextField(blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    payment_intent_id = models.CharField(max_length=120, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    ordered_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user_email", "status"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"BacklinkOrder<{self.id} {self.status} {self.product.domain}>"


class BlogPost(models.Model):
    """A blog post published to a satellite site — stored in the SHARED blog DB.

    No FKs (it lives in a separate DB the satellite sites read standalone). ``site``
    separates the 5 sites; ``brand_ref`` + ``brand_url`` let Signalor's "Our
    backlinks" tab filter to a brand. Signalor writes these rows; sites read them.
    Routed to the ``blog`` database via config.db_router.BlogRouter.
    """

    class Site(models.TextChoices):
        RESEARCH = "research", "Research"
        LISTICALS = "listicals", "Listicals"
        MARKET_TRENDS = "market_trends", "Market Trends"
        COMPARISON = "comparison", "Comparison"
        STEP_GUIDE = "step_guide", "Step Guide"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    site = models.CharField(max_length=20, choices=Site.choices, db_index=True)
    slug = models.CharField(max_length=160, db_index=True)
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default="")
    content_html = models.TextField(blank=True, default="")
    image_url = models.URLField(max_length=2048, blank=True, default="")
    category = models.CharField(max_length=80, blank=True, default="")
    # The brand domain the post links back to (the backlink target).
    brand_url = models.URLField(max_length=2048, blank=True, default="")
    # String ref to the Signalor org/brand (no cross-DB FK) for dashboard filtering.
    brand_ref = models.CharField(max_length=64, blank=True, default="", db_index=True)
    source = models.CharField(max_length=20, default="signalor")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PUBLISHED)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["site", "slug"], name="uniq_blogpost_site_slug"),
        ]
        indexes = [
            models.Index(fields=["site", "status"]),
            models.Index(fields=["brand_ref"]),
        ]

    def __str__(self):
        return f"BlogPost<{self.site}/{self.slug}>"

