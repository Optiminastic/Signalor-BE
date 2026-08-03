"""Recommendations, the user tasks derived from them, and gamification."""

from django.db import models

from .run import AnalysisRun


class Recommendation(models.Model):
    class Priority(models.TextChoices):
        CRITICAL = "critical"
        HIGH = "high"
        MEDIUM = "medium"
        LOW = "low"

    class Source(models.TextChoices):
        ANALYZER = "analyzer", "Analyzer"
        AI_INSIGHT = "ai_insight", "AI Insight"
        GEO_SIGNAL = "geo_signal", "GEO Signal"

    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="recommendations")
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
    # Where this rec came from — "analyzer" (pipeline), "ai_insight" (GA/GSC AI
    # insights), or "geo_signal" (measured prompt/citation/competitor gaps).
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.ANALYZER, db_index=True)

    # Concrete per-page evidence backing this task (e.g. citation_count, word_count,
    # top_repeated). Grounds the description instead of a generic template string.
    evidence = models.JSONField(default=dict, blank=True)
    # URLs this finding was detected on, after cross-page dedupe (one task, N pages).
    affected_pages = models.JSONField(default=list, blank=True)
    # LLM-drafted concrete fix content (FAQ Q&A, rewritten paragraph, citations).
    # Shape: {type, data, prompt_version, model, content_hash, generated_at}. Empty
    # dict means enrichment was skipped/failed and the static `action` is the fallback.
    generated_content = models.JSONField(default=dict, blank=True)

    # ── Daily re-check / re-prioritize state ──────────────────────────────────
    # Last time the daily job re-verified this fix against the live site.
    last_checked_at = models.DateTimeField(null=True, blank=True)
    # Re-ranked order among still-open recs for this run (1 = highest). 0 = unranked.
    daily_priority_rank = models.IntegerField(default=0)
    # The single "priority fix of the day" surfaced at the top of Tasks.
    is_top_fix = models.BooleanField(default=False)

    class Meta:
        ordering = ["priority", "pillar"]

    def __str__(self):
        return f"[{self.priority}] {self.title}"


class TaskSatisfaction(models.Model):
    """Cross-run memory of which (page, finding) pairs are already satisfied.

    One upserted row per (organization, page_url, finding_code). ``content_hash``
    is the page's visible-text hash when the pair was last confirmed done — a
    content change invalidates the entry so a genuine regression can resurface.

    Written by the satisfaction gate, the daily recheck, auto-fix verification,
    and user "mark done"; read by the gate to suppress already-done tasks without
    re-verifying unchanged pages. Only meaningful for runs with an organization
    (persistent brands); anonymous free-tool runs don't use it.
    """

    class Source(models.TextChoices):
        HEURISTIC = "heuristic", "Heuristic"
        LLM = "llm", "LLM"
        AUTOFIX = "autofix", "Auto-fix"
        RECHECK = "recheck", "Daily recheck"
        USER = "user", "User"

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="task_satisfactions",
    )
    page_url = models.CharField(max_length=2048)
    finding_code = models.CharField(max_length=80, db_index=True)
    content_hash = models.CharField(max_length=64, blank=True, default="")
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.HEURISTIC)
    confidence = models.FloatField(default=1.0)
    evidence = models.JSONField(default=dict, blank=True)
    verified_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "page_url", "finding_code"],
                name="uniq_task_satisfaction",
            ),
        ]

    def __str__(self):
        return f"TaskSatisfaction[{self.finding_code}] {self.page_url}"


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
    # Agency teammate this task is assigned to (blank = unassigned). Distinct from
    # user_email (the owner/creator — the agency admin for materialized tasks).
    assignee_email = models.EmailField(db_index=True, blank=True, default="")
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

    status = models.CharField(max_length=20, choices=ActionStatus.choices, default=ActionStatus.PENDING)

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

    # Result of the last live re-crawl verification (why a fix did / didn't pass).
    verification_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user_email", "status"]),
            models.Index(fields=["user_email", "-created_at"]),
            models.Index(fields=["analysis_run_id"]),
            # Agency-member task list filters on assignee_email then status
            # (UserActionListView); mirror the owner (user_email, status) index.
            models.Index(fields=["assignee_email", "status"]),
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
        from django.db import transaction
        from django.utils import timezone

        with transaction.atomic():
            # Lock and reload this row so two concurrent action completions can't
            # both read the same totals and lose one update — transaction.atomic()
            # alone does NOT prevent a lost update under READ COMMITTED.
            locked = type(self).objects.select_for_update().get(pk=self.pk)

            locked.total_points += points
            locked.current_level_points += points
            locked.points_this_week += points
            locked.points_this_month += points
            locked.total_actions_completed += 1

            # Update streak
            today = timezone.now().date()
            if locked.last_action_date:
                days_diff = (today - locked.last_action_date).days
                if days_diff == 1:
                    locked.current_streak += 1
                elif days_diff > 1:
                    locked.current_streak = 1
            else:
                locked.current_streak = 1

            if locked.current_streak > locked.longest_streak:
                locked.longest_streak = locked.current_streak

            locked.last_action_date = today

            # Check for level up
            did_level_up = False

            while locked.current_level_points >= self._points_for_level(locked.level):
                locked.current_level_points -= self._points_for_level(locked.level)
                if locked.level < self.Level.LEGEND:
                    locked.level += 1
                    locked.points_to_next_level = self._points_for_level(locked.level)
                    did_level_up = True

            locked.save()

        # Reflect the persisted state onto this instance for callers holding it.
        self.refresh_from_db()
        return locked.level, did_level_up

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

    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="auto_fix_jobs")
    recommendation = models.ForeignKey(Recommendation, on_delete=models.CASCADE, related_name="auto_fix_jobs")
    integration = models.ForeignKey(
        "integrations.Integration",
        on_delete=models.CASCADE,
        related_name="auto_fix_jobs",
        null=True,
        blank=True,
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

