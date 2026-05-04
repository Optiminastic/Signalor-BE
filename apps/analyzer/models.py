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
    content_hash = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    # User-selected prompts from verified onboarding / post-checkout launch (empty for other flows)
    onboarding_prompts = models.JSONField(default=list, blank=True)
    llm_logs = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["status"]),
            models.Index(fields=["slug"]),
            models.Index(fields=["email", "status"]),
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
    content_hash = models.CharField(max_length=64, blank=True, default="")
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
    # Stable pipeline key (e.g. no_citations) for verify routing; blank for legacy rows.
    finding_code = models.CharField(max_length=80, blank=True, default="")
    why = models.CharField(max_length=200, blank=True, default="")
    # Structured step-by-step guide + gamification metadata
    steps = models.JSONField(default=list, blank=True)
    xp_reward = models.IntegerField(default=0)
    difficulty = models.CharField(max_length=20, blank=True, default="")  # easy, medium, hard
    estimated_minutes = models.IntegerField(default=0)
    # The finding key that triggered this recommendation (e.g. "no_h1", "no_citations")
    finding_key = models.CharField(max_length=80, blank=True, default="")

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
    class SearchIntent(models.TextChoices):
        """Why the user is asking (GEO / prompt strategy)."""

        BRAND = "brand", "Brand"
        INFORMATIONAL = "informational", "Information"
        TRANSACTIONAL = "transactional", "Transactional"

    class PromptSurfaceType(models.TextChoices):
        """Shape of the query vs brand & competition (classic AI-search buckets)."""

        ORGANIC = "organic", "Organic"
        BRANDED = "branded", "Brand"
        COMPETITIVE = "competitive", "Competition"

    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.CASCADE, related_name="prompt_tracks"
    )
    prompt_text = models.TextField()
    is_custom = models.BooleanField(default=False)
    intent = models.CharField(
        max_length=20,
        choices=SearchIntent.choices,
        default=SearchIntent.INFORMATIONAL,
    )
    prompt_type = models.CharField(
        max_length=20,
        choices=PromptSurfaceType.choices,
        default=PromptSurfaceType.ORGANIC,
    )
    score = models.FloatField(default=0.0)

    # 5-Factor AI Visibility Ranking Scores (all 0.0–1.0)
    authority_score = models.FloatField(default=0.0)        # Factor 1 — 40% weight
    content_quality_score = models.FloatField(default=0.0)  # Factor 2 — 35% weight
    structural_score = models.FloatField(default=0.0)       # Factor 3 — 25% weight
    semantic_score = models.FloatField(default=0.0)         # Factor 4 — supplementary
    third_party_score = models.FloatField(default=0.0)      # Factor 5 — supplementary

    created_at = models.DateTimeField(auto_now_add=True)
    # Soft-delete so that deleting a prompt does NOT free a plan-limit slot.
    # Active (visible) prompts are those with deleted_at IS NULL; all rows
    # still count toward `max_prompts` usage.
    deleted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"PromptTrack #{self.pk} — {self.prompt_text[:60]}"

    class Meta:
        indexes = [
            models.Index(fields=["analysis_run", "score", "created_at"]),
        ]


class PromptResult(models.Model):
    class Engine(models.TextChoices):
        CHATGPT = "chatgpt", "ChatGPT"
        CLAUDE = "claude", "Claude"
        GEMINI = "gemini", "Gemini"
        PERPLEXITY = "perplexity", "Perplexity"
        GOOGLE = "google", "Google"
        BING = "bing", "Bing"

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
        indexes = [
            models.Index(fields=["prompt_track", "engine"]),
            models.Index(fields=["prompt_track", "brand_mentioned"]),
        ]

    def __str__(self):
        return f"PromptResult [{self.engine}] {'✓' if self.brand_mentioned else '✗'} {self.sentiment}"


class PromptCitation(models.Model):
    """
    URLs cited by an AI engine (or search engine) when responding to a tracked prompt.
    Captures source attribution so "pages AI loves" roll-ups and competitor gap analysis
    can be derived per-run without re-parsing response text.
    """
    prompt_result = models.ForeignKey(
        PromptResult, on_delete=models.CASCADE, related_name="citations"
    )
    url = models.URLField(max_length=2048)
    domain = models.CharField(max_length=255, blank=True, default="", db_index=True)
    title = models.CharField(max_length=512, blank=True, default="")
    snippet = models.TextField(blank=True, default="")
    position = models.IntegerField(default=0)
    is_brand = models.BooleanField(default=False, db_index=True)
    is_competitor = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["prompt_result_id", "position", "id"]
        indexes = [
            models.Index(fields=["domain", "is_brand"]),
            models.Index(fields=["domain", "is_competitor"]),
        ]

    def __str__(self):
        return f"Citation {self.domain} (brand={self.is_brand}, rival={self.is_competitor})"


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


class AutoFixJob(models.Model):
    class Status(models.TextChoices):
        PREVIEW = "preview"
        PENDING = "pending"
        RUNNING = "running"
        SUCCESS = "success"
        PARTIAL = "partial"
        FAILED = "failed"
        MANUAL = "manual"
        SKIPPED = "skipped"
        VERIFIED = "verified"

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
        "integrations.Integration", on_delete=models.CASCADE, related_name="auto_fix_jobs",
        null=True, blank=True,
    )
    fix_type = models.CharField(max_length=30)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payload_sent = models.JSONField(default=dict, blank=True)
    response_data = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"AutoFix<{self.fix_type} {self.status}>"


class SitemapAudit(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        COMPLETE = "complete"
        FAILED = "failed"

    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.CASCADE, related_name="sitemap_audits"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
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

    audit = models.ForeignKey(
        SitemapAudit, on_delete=models.CASCADE, related_name="pages"
    )
    url = models.URLField(max_length=2048)
    path = models.CharField(max_length=2048, blank=True, default="")
    final_url = models.URLField(max_length=2048, blank=True, default="")

    state = models.CharField(
        max_length=12, choices=State.choices, default=State.QUEUED, db_index=True
    )
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
    severity = models.CharField(
        max_length=8, choices=Severity.choices, default=Severity.OK, db_index=True
    )
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


class AgentLogEntry(models.Model):
    """Skeleton for future ingestion of AI crawler hits (Cloudflare Logpush /
    Vercel Edge logs). No ingestion in v1 — the table exists so the frontend
    Agent-log tab can query an empty but well-typed endpoint."""

    class Source(models.TextChoices):
        CLOUDFLARE = "cloudflare"
        VERCEL = "vercel"
        MANUAL = "manual"

    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.CASCADE, related_name="agent_log_entries"
    )
    bot_name = models.CharField(max_length=64, db_index=True)
    path = models.CharField(max_length=2048)
    status_code = models.IntegerField(default=0)
    ts = models.DateTimeField(db_index=True)
    source = models.CharField(
        max_length=16, choices=Source.choices, default=Source.MANUAL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-ts"]
        indexes = [models.Index(fields=["analysis_run", "bot_name"])]

    def __str__(self):
        return f"AgentLogEntry<{self.bot_name} {self.path}>"


class SchemaWatch(models.Model):
    """A run of the Schema Watchtower — validates JSON-LD on a set of URLs
    (products, articles, FAQs) for breakage and drift. v1 is a static
    validator; v2 will diff against a stored baseline for drift detection."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        COMPLETE = "complete"
        FAILED = "failed"

    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.CASCADE, related_name="schema_watches"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
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

    watch = models.ForeignKey(
        SchemaWatch, on_delete=models.CASCADE, related_name="pages"
    )
    url = models.URLField(max_length=2048)
    path = models.CharField(max_length=2048, blank=True, default="")
    page_kind = models.CharField(max_length=32, blank=True, default="")
    status_code = models.IntegerField(default=0)

    schema_types = models.JSONField(default=list, blank=True)
    jsonld_count = models.IntegerField(default=0)
    raw_jsonld = models.JSONField(default=list, blank=True)

    severity = models.CharField(
        max_length=8, choices=Severity.choices, default=Severity.OK, db_index=True
    )
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


class RankAudit(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        COMPLETE = "complete"
        FAILED = "failed"

    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.CASCADE, related_name="rank_audits"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    progress = models.IntegerField(default=0)
    total_queries = models.IntegerField(default=0)
    queries_done = models.IntegerField(default=0)

    avg_brand_mentions = models.FloatField(default=0.0)
    avg_top3_brand_rate = models.FloatField(default=0.0)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"RankAudit<{self.analysis_run_id} {self.status} {self.progress}%>"


class RankQuery(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        DONE = "done"
        FAILED = "failed"

    audit = models.ForeignKey(
        RankAudit, on_delete=models.CASCADE, related_name="queries"
    )
    prompt_text = models.TextField()
    rank = models.IntegerField(default=0)
    brand_mention_count = models.IntegerField(default=0)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["audit_id", "rank", "id"]

    def __str__(self):
        return f"RankQuery<{self.audit_id} #{self.rank} {self.status}>"


class RankResult(models.Model):
    class Surface(models.TextChoices):
        GOOGLE = "google"
        REDDIT = "reddit"
        QUORA = "quora"
        AI = "ai"

    query = models.ForeignKey(
        RankQuery, on_delete=models.CASCADE, related_name="results"
    )
    surface = models.CharField(
        max_length=16, choices=Surface.choices, db_index=True
    )
    position = models.IntegerField()
    url = models.URLField(max_length=2048, blank=True, default="")
    domain = models.CharField(max_length=255, blank=True, default="")
    title = models.CharField(max_length=300, blank=True, default="")
    snippet = models.TextField(blank=True, default="")

    # AI-surface specific — blank for SERP rows
    engine = models.CharField(max_length=64, blank=True, default="")
    response_text = models.TextField(blank=True, default="")

    class Sentiment(models.TextChoices):
        POSITIVE = "positive"
        NEUTRAL = "neutral"
        NEGATIVE = "negative"

    sentiment = models.CharField(
        max_length=10, choices=Sentiment.choices, default=Sentiment.NEUTRAL
    )

    is_brand_mentioned = models.BooleanField(default=False)
    competitors_mentioned = models.JSONField(default=list, blank=True)

    upvotes = models.IntegerField(null=True, blank=True)
    subreddit = models.CharField(max_length=120, blank=True, default="")

    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["query_id", "surface", "position"]
        indexes = [
            models.Index(fields=["query", "surface", "position"]),
        ]

    def __str__(self):
        return f"RankResult<{self.query_id} {self.surface}#{self.position}>"


# ─────────────────────────────────────────────────────────────────────────────
# Backlink marketplace
# Connects the brand to paid backlink providers (FATJOE, The HOTH, Adsy, …).
# Catalog is cached locally; orders are placed through the provider's API and
# tracked here through delivery.
# ─────────────────────────────────────────────────────────────────────────────


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
    link_type = models.CharField(
        max_length=20, choices=LinkType.choices, default=LinkType.GUEST_POST
    )
    domain_authority = models.IntegerField(null=True, blank=True)  # 0–100 scale
    domain_rank = models.IntegerField(null=True, blank=True)        # 0–1000 scale
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
            models.UniqueConstraint(
                fields=["provider", "sku"], name="uniq_provider_sku"
            ),
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

    provider = models.ForeignKey(
        BacklinkProvider, on_delete=models.PROTECT, related_name="orders"
    )
    product = models.ForeignKey(
        BacklinkProduct, on_delete=models.PROTECT, related_name="orders"
    )
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
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.DRAFT, db_index=True
    )

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


class BrandKit(models.Model):
    """Per-run cached brand submission kit (LLM-generated)."""
    analysis_run = models.OneToOneField(
        AnalysisRun, on_delete=models.CASCADE, related_name="brand_kit"
    )
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"BrandKit<run={self.analysis_run_id}>"


class PromptWikipediaDraft(models.Model):
    """Per-prompt cached Wikipedia draft kit (LLM-generated)."""
    prompt_track = models.OneToOneField(
        PromptTrack, on_delete=models.CASCADE, related_name="wikipedia_draft"
    )
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"PromptWikipediaDraft<track={self.prompt_track_id}>"


class ChatMessage(models.Model):
    """Persistent AI assistant chat history per analysis run."""

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.CASCADE, related_name="chat_messages"
    )
    role = models.CharField(max_length=12, choices=Role.choices)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["analysis_run", "created_at"])]

    def __str__(self):
        return f"ChatMessage<run={self.analysis_run_id} {self.role} {self.created_at:%Y-%m-%d %H:%M}>"


class PromptSchemaArtifact(models.Model):
    """A saved artifact (answer paragraph or JSON-LD) generated for a prompt."""

    class SchemaType(models.TextChoices):
        FAQ = "faq", "FAQ"
        ARTICLE = "article", "Article"
        PERSON = "person", "Person"
        ORGANIZATION = "organization", "Organization"
        ANSWER = "answer", "Direct answer"

    prompt_track = models.ForeignKey(
        PromptTrack, on_delete=models.CASCADE, related_name="schema_artifacts"
    )
    schema_type = models.CharField(max_length=24, choices=SchemaType.choices)
    output = models.TextField()
    explanation = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("prompt_track", "schema_type")]
        indexes = [models.Index(fields=["prompt_track", "schema_type"])]

    def __str__(self):
        return f"PromptSchemaArtifact<track={self.prompt_track_id} {self.schema_type}>"


class DomainAnalyticsSnapshot(models.Model):
    """
    Per-run cached DataForSEO Domain Analytics snapshot — estimated organic
    traffic, top keywords, top pages, and per-country geographic breakdown.
    Refreshed on demand; default TTL is 7 days.
    """
    analysis_run = models.OneToOneField(
        AnalysisRun, on_delete=models.CASCADE, related_name="domain_analytics"
    )
    overview = models.JSONField(default=dict)
    top_keywords = models.JSONField(default=list)
    top_pages = models.JSONField(default=list)
    geo_distribution = models.JSONField(default=dict)
    synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"DomainAnalyticsSnapshot<run={self.analysis_run_id}>"


class ContentSuggestion(models.Model):
    """AI-proposed content edit for a single page in the Optimisation tab.

    A suggestion targets one editor field (title, meta_description, body_html,
    schema_jsonld). The user can `Use this` (stages it into the editor draft)
    or `Dismiss`. The actual save to the live site goes through the existing
    plugin pipeline and is logged separately as an AutoFixJob.
    """

    PROPOSED = "proposed"
    USED = "used"
    DISMISSED = "dismissed"
    STATUS_CHOICES = [
        (PROPOSED, "proposed"),
        (USED, "used"),
        (DISMISSED, "dismissed"),
    ]

    TARGET_TITLE = "title"
    TARGET_META = "meta_description"
    TARGET_BODY = "body_html"
    TARGET_SCHEMA = "schema_jsonld"
    TARGET_FIELD_CHOICES = [
        (TARGET_TITLE, "title"),
        (TARGET_META, "meta_description"),
        (TARGET_BODY, "body_html"),
        (TARGET_SCHEMA, "schema_jsonld"),
    ]

    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="content_suggestions",
    )
    url = models.CharField(max_length=2048)
    title = models.CharField(max_length=255)
    rationale = models.TextField()
    target_field = models.CharField(max_length=32, choices=TARGET_FIELD_CHOICES)
    current_excerpt = models.TextField(blank=True)
    proposed_value = models.TextField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PROPOSED)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["analysis_run", "url", "status"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"ContentSuggestion<{self.target_field} {self.url}>"
