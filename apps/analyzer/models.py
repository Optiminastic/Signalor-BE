import secrets
from datetime import time

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
            models.Index(fields=["slug"]),
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
    tier = models.CharField(max_length=20, blank=True, default="")
    target_market = models.CharField(max_length=80, blank=True, default="")
    geography = models.CharField(max_length=80, blank=True, default="")
    pricing_model = models.CharField(max_length=80, blank=True, default="")
    estimated_revenue_band = models.CharField(max_length=40, blank=True, default="")
    positioning = models.CharField(max_length=240, blank=True, default="")
    relevance_score = models.IntegerField(null=True, blank=True)
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


# ============ Gamification Models ============

class UserAction(models.Model):
    """Tracks user actions taken to improve their GEO score"""
    
    class ActionType(models.TextChoices):
        # Content actions
        ADD_FAQ = "add_faq", "Add FAQ Section"
        ADD_STRUCTURE = "add_structure", "Improve Content Structure"
        ADD_CITATIONS = "add_citations", "Add Citations & References"
        IMPROVE_READABILITY = "improve_readability", "Improve Readability"
        
        # Schema actions
        ADD_SCHEMA = "add_schema", "Add Schema Markup"
        ADD_ARTICLE_SCHEMA = "add_article_schema", "Add Article Schema"
        ADD_FAQ_SCHEMA = "add_faq_schema", "Add FAQ Schema"
        
        # Technical actions
        ADD_ROBOTS = "add_robots", "Create robots.txt"
        ADD_SITEMAP = "add_sitemap", "Create sitemap.xml"
        ADD_LLMS_TXT = "add_llms_txt", "Create llms.txt"
        ENABLE_HTTPS = "enable_https", "Enable HTTPS"
        
        # E-E-A-T actions
        ADD_AUTHOR = "add_author", "Add Author Information"
        ADD_ABOUT = "add_about", "Add About Page"
        ADD_CONTACT = "add_contact", "Add Contact Page"
        ADD_PRIVACY = "add_privacy", "Add Privacy Policy"
        
        # Entity actions
        CREATE_WIKIPEDIA = "create_wikipedia", "Create Wikipedia Page"
        ADD_SOCIAL = "add_social", "Add Social Profiles"
        
        # Brand actions
        POST_REDDIT = "post_reddit", "Post on Reddit"
        POST_MEDIUM = "post_medium", "Publish on Medium"
        BUILD_BACKLINKS = "build_backlinks", "Build Backlinks"
    
    class ActionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        VERIFIED = "verified", "Verified (Score Improved)"

    user_email = models.EmailField(db_index=True)
    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.CASCADE, related_name="user_actions", null=True, blank=True
    )
    recommendation = models.ForeignKey(
        Recommendation, on_delete=models.SET_NULL, null=True, blank=True, related_name="user_actions"
    )
    
    action_type = models.CharField(max_length=30, choices=ActionType.choices)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    points_value = models.IntegerField(default=10)
    
    status = models.CharField(
        max_length=20, choices=ActionStatus.choices, default=ActionStatus.PENDING
    )
    
    # Tracking
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    
    # Score tracking
    score_before = models.FloatField(null=True, blank=True)
    score_after = models.FloatField(null=True, blank=True)
    score_improvement = models.FloatField(null=True, blank=True)
    
    # Notes from user
    notes = models.TextField(blank=True, default="")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user_email", "status"]),
            models.Index(fields=["user_email", "-created_at"]),
            models.Index(fields=["analysis_run_id"]),
        ]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.title} - {self.user_email}"

    def complete(self):
        """Mark action as completed"""
        from django.utils import timezone
        self.status = self.ActionStatus.COMPLETED
        self.completed_at = timezone.now()
        self.save()

    def verify(self, new_score: float):
        """Verify action and calculate improvement"""
        from django.utils import timezone
        self.status = self.ActionStatus.VERIFIED
        self.verified_at = timezone.now()
        self.score_after = new_score
        if self.score_before:
            self.score_improvement = new_score - self.score_before
        self.save()


class UserGamification(models.Model):
    """User gamification profile - points, levels, achievements"""
    
    class Level(models.IntegerChoices):
        BEGINNER = 1, "Beginner"
        LEARNER = 2, "Learner" 
        IMPLEMENTER = 3, "Implementer"
        OPTIMIZER = 4, "Optimizer"
        EXPERT = 5, "Expert"
        MASTER = 6, "Master"
        LEGEND = 7, "Legend"

    user_email = models.EmailField(unique=True, db_index=True)
    
    # Points system
    total_points = models.IntegerField(default=0)
    points_this_week = models.IntegerField(default=0)
    points_this_month = models.IntegerField(default=0)
    
    # Level system
    level = models.IntegerField(choices=Level.choices, default=Level.BEGINNER)
    current_level_points = models.IntegerField(default=0)  # Points in current level
    points_to_next_level = models.IntegerField(default=100)
    
    # Streaks
    current_streak = models.IntegerField(default=0)  # Days in a row
    longest_streak = models.IntegerField(default=0)
    last_action_date = models.DateField(null=True, blank=True)
    
    # Stats
    total_actions_completed = models.IntegerField(default=0)
    total_actions_verified = models.IntegerField(default=0)
    total_score_improvement = models.FloatField(default=0)
    
    # Achievements (stored as list of achievement codes)
    achievements = models.JSONField(default=list)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "User Gamification"

    def __str__(self):
        return f"{self.user_email} - Level {self.get_level_display()} ({self.total_points} pts)"

    @property
    def level_name(self) -> str:
        return self.get_level_display()

    @property
    def level_progress(self) -> float:
        """Returns level progress as percentage (0-100)"""
        total_for_level = self._points_for_level(self.level)
        return (self.current_level_points / total_for_level) * 100 if total_for_level > 0 else 0

    def _points_for_level(self, level: int) -> int:
        """Calculate points needed for a specific level"""
        # Exponential scaling: 100, 250, 500, 1000, 2000, 4000, 8000
        return int(100 * (2.5 ** (level - 1)))

    def add_points(self, points: int) -> tuple[int, bool]:
        """
        Add points to user and handle level ups
        Returns (new_level, did_level_up)
        """
        from django.utils import timezone
        from django.db import transaction
        
        with transaction.atomic():
            self.total_points += points
            self.current_level_points += points
            self.points_this_week += points
            self.points_this_month += points
            self.total_actions_completed += 1
            
            # Update streak
            today = timezone.now().date()
            if self.last_action_date:
                days_diff = (today - self.last_action_date).days
                if days_diff == 1:
                    self.current_streak += 1
                elif days_diff > 1:
                    self.current_streak = 1
            else:
                self.current_streak = 1
            
            if self.current_streak > self.longest_streak:
                self.longest_streak = self.current_streak
            
            self.last_action_date = today
            
            # Check for level up
            old_level = self.level
            did_level_up = False
            
            while self.current_level_points >= self._points_for_level(self.level):
                self.current_level_points -= self._points_for_level(self.level)
                if self.level < self.Level.LEGEND:
                    self.level += 1
                    self.points_to_next_level = self._points_for_level(self.level)
                    did_level_up = True
            
            self.save()
            
            return self.level, did_level_up

    def check_achievements(self) -> list[str]:
        """Check and award new achievements"""
        new_achievements = []
        
        achievement_conditions = {
            "first_action": self.total_actions_completed >= 1,
            "ten_actions": self.total_actions_completed >= 10,
            "fifty_actions": self.total_actions_completed >= 50,
            "hundred_actions": self.total_actions_completed >= 100,
            "first_verified": self.total_actions_verified >= 1,
            "ten_verified": self.total_actions_verified >= 10,
            "streak_3": self.longest_streak >= 3,
            "streak_7": self.longest_streak >= 7,
            "streak_30": self.longest_streak >= 30,
            "level_2": self.level >= 2,
            "level_3": self.level >= 3,
            "level_5": self.level >= 5,
            "level_7": self.level >= 7,
            "points_100": self.total_points >= 100,
            "points_500": self.total_points >= 500,
            "points_1000": self.total_points >= 1000,
            "points_5000": self.total_points >= 5000,
            "improvement_5": self.total_score_improvement >= 5,
            "improvement_10": self.total_score_improvement >= 10,
            "improvement_20": self.total_score_improvement >= 20,
        }
        
        for code, condition in achievement_conditions.items():
            if condition and code not in self.achievements:
                self.achievements.append(code)
                new_achievements.append(code)
        
        if new_achievements:
            self.save()
        
        return new_achievements


# Achievement definitions for UI display
ACHIEVEMENTS_INFO = {
    "first_action": {
        "name": "First Step",
        "description": "Complete your first action",
        "icon": "🚀",
        "points": 10,
    },
    "ten_actions": {
        "name": "Getting Started",
        "description": "Complete 10 actions",
        "icon": "📈",
        "points": 50,
    },
    "fifty_actions": {
        "name": "Dedicated",
        "description": "Complete 50 actions",
        "icon": "⭐",
        "points": 200,
    },
    "hundred_actions": {
        "name": "Century Club",
        "description": "Complete 100 actions",
        "icon": "🏆",
        "points": 500,
    },
    "first_verified": {
        "name": "Proof of Work",
        "description": "Get your first action verified",
        "icon": "✅",
        "points": 25,
    },
    "ten_verified": {
        "name": "Verified Expert",
        "description": "Get 10 actions verified",
        "icon": "💯",
        "points": 150,
    },
    "streak_3": {
        "name": "On a Roll",
        "description": "3 day action streak",
        "icon": "🔥",
        "points": 30,
    },
    "streak_7": {
        "name": "Week Warrior",
        "description": "7 day action streak",
        "icon": "⚡",
        "points": 100,
    },
    "streak_30": {
        "name": "Monthly Master",
        "description": "30 day action streak",
        "icon": "🌟",
        "points": 500,
    },
    "level_2": {
        "name": "Level 2 Unlocked",
        "description": "Reach Learner level",
        "icon": "📚",
        "points": 50,
    },
    "level_3": {
        "name": "Level 3 Unlocked",
        "description": "Reach Implementer level",
        "icon": "🛠️",
        "points": 100,
    },
    "level_5": {
        "name": "Level 5 Unlocked",
        "description": "Reach Expert level",
        "icon": "🎯",
        "points": 250,
    },
    "level_7": {
        "name": "Level 7 Unlocked",
        "description": "Reach Legend level",
        "icon": "👑",
        "points": 1000,
    },
    "points_100": {
        "name": "Centurion",
        "description": "Earn 100 total points",
        "icon": "💰",
        "points": 25,
    },
    "points_500": {
        "name": "Half Grand",
        "description": "Earn 500 total points",
        "icon": "💎",
        "points": 75,
    },
    "points_1000": {
        "name": "Grand Club",
        "description": "Earn 1000 total points",
        "icon": "🏅",
        "points": 150,
    },
    "points_5000": {
        "name": "GEO Master",
        "description": "Earn 5000 total points",
        "icon": "🏆",
        "points": 500,
    },
    "improvement_5": {
        "name": "Rising Star",
        "description": "Improve score by 5 points",
        "icon": "📈",
        "points": 50,
    },
    "improvement_10": {
        "name": "Big Improver",
        "description": "Improve score by 10 points",
        "icon": "🚀",
        "points": 100,
    },
    "improvement_20": {
        "name": "Transformation",
        "description": "Improve score by 20 points",
        "icon": "🌟",
        "points": 250,
    },
}

# Action templates for quick action creation
ACTION_TEMPLATES = {
    "add_faq": {
        "title": "Add FAQ Section",
        "description": "Add a comprehensive FAQ section to your page",
        "points": 50,
        "category": "content",
    },
    "add_schema": {
        "title": "Add Schema Markup",
        "description": "Implement JSON-LD schema markup on your website",
        "points": 75,
        "category": "schema",
    },
    "add_robots": {
        "title": "Create robots.txt",
        "description": "Create a proper robots.txt file",
        "points": 25,
        "category": "technical",
    },
    "add_author": {
        "title": "Add Author Bio",
        "description": "Add author information and bio to your content",
        "points": 40,
        "category": "eeat",
    },
    "post_reddit": {
        "title": "Post on Reddit",
        "description": "Share your expertise on relevant Reddit communities",
        "points": 60,
        "category": "entity",
    },
    "enable_https": {
        "title": "Enable HTTPS",
        "description": "Ensure your site uses HTTPS",
        "points": 30,
        "category": "technical",
    },
}


class PromptTrack(models.Model):
    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.CASCADE, related_name="prompt_tracks"
    )
    prompt_text = models.TextField()
    is_custom = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"PromptTrack #{self.pk} — {self.prompt_text[:60]}"


class PromptResult(models.Model):
    class Engine(models.TextChoices):
        CHATGPT = "chatgpt", "ChatGPT"
        CLAUDE = "claude", "Claude"
        GEMINI = "gemini", "Gemini"
        PERPLEXITY = "perplexity", "Perplexity"

    class Sentiment(models.TextChoices):
        POSITIVE = "positive", "Positive"
        NEUTRAL = "neutral", "Neutral"
        NEGATIVE = "negative", "Negative"

    prompt_track = models.ForeignKey(
        PromptTrack, on_delete=models.CASCADE, related_name="results"
    )
    engine = models.CharField(max_length=20, choices=Engine.choices)
    response_text = models.TextField(blank=True)
    brand_mentioned = models.BooleanField(default=False)
    sentiment = models.CharField(
        max_length=10, choices=Sentiment.choices, default=Sentiment.NEUTRAL
    )
    confidence = models.FloatField(default=0.0)
    rank_position = models.IntegerField(default=0)
    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["checked_at"]

    def __str__(self):
        return f"PromptResult [{self.engine}] {'✓' if self.brand_mentioned else '✗'} {self.sentiment}"


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


class ScheduledAnalysis(models.Model):
    class Frequency(models.TextChoices):
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


class AutoFixJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCESS = "success"
        PARTIAL = "partial"
        FAILED = "failed"

    class FixType(models.TextChoices):
        SCHEMA_MARKUP = "schema_markup"
        META_DESCRIPTION = "meta_description"
        FAQ_SECTION = "faq_section"

    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.CASCADE, related_name="auto_fix_jobs"
    )
    recommendation = models.ForeignKey(
        Recommendation, on_delete=models.CASCADE, related_name="auto_fix_jobs"
    )
    integration = models.ForeignKey(
        "integrations.Integration", on_delete=models.CASCADE, related_name="auto_fix_jobs"
    )
    fix_type = models.CharField(max_length=30, choices=FixType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payload_sent = models.JSONField(default=dict, blank=True)
    response_data = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"AutoFix<{self.fix_type} {self.status}>"
