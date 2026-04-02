from rest_framework import serializers

from .models import (
    AIVisibilityProbe,
    AnalysisRun,
    BrandVisibility,
    Competitor,
    PageScore,
    Recommendation,
    UserAction,
    UserGamification,
    BlogAutomationConfig,
    BlogAutomationJob,
    PromptTrack,
    PromptResult,
    ScheduledAnalysis,
    ACHIEVEMENTS_INFO,
    ACTION_TEMPLATES,
)


class RecommendationSerializer(serializers.ModelSerializer):
    can_auto_fix = serializers.SerializerMethodField()

    class Meta:
        model = Recommendation
        fields = [
            "id", "pillar", "priority", "title", "description",
            "action", "impact_estimate", "category", "can_auto_fix", "why",
        ]

    # Title keywords that indicate manual-only recommendations
    MANUAL_TITLE_KEYWORDS = [
        "sitemap", "enable https", "page load speed", "improve page load",
        "crawler blocked", "blocks automated", "too slow to crawl",
        "wikipedia", "reddit", "medium", "google ai overview",
        "brand into ai", "social profile", "brand website signal",
    ]

    def get_can_auto_fix(self, obj):
        title_lower = (obj.title or "").lower()
        for kw in self.MANUAL_TITLE_KEYWORDS:
            if kw in title_lower:
                return False
        return True


class AIVisibilityProbeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIVisibilityProbe
        fields = [
            "id", "prompt_used", "llm_response", "brand_mentioned", "confidence",
        ]


class PageScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = PageScore
        fields = [
            "id", "url", "content_score", "content_details",
            "schema_score", "schema_details", "eeat_score", "eeat_details",
            "technical_score", "technical_details", "entity_score", "entity_details",
            "ai_visibility_score", "ai_visibility_details", "composite_score",
        ]


class BrandVisibilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = BrandVisibility
        fields = [
            "google_score", "google_details",
            "reddit_score", "reddit_details",
            "medium_score", "medium_details",
            "web_mentions_score", "web_mentions_details",
            "overall_score",
        ]


class CompetitorSerializer(serializers.ModelSerializer):
    page_score = PageScoreSerializer(read_only=True)

    class Meta:
        model = Competitor
        fields = [
            "id", "name", "url", "industry",
            "tier", "target_market", "geography", "pricing_model",
            "estimated_revenue_band", "positioning", "relevance_score",
            "composite_score", "scored", "page_score",
        ]


class AnalysisRunListSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnalysisRun
        fields = [
            "id", "slug", "url", "country", "run_type", "status", "progress",
            "composite_score", "created_at",
        ]


class AnalysisRunDetailSerializer(serializers.ModelSerializer):
    page_scores = PageScoreSerializer(many=True, read_only=True)
    competitors = CompetitorSerializer(many=True, read_only=True)
    recommendations = RecommendationSerializer(many=True, read_only=True)
    ai_probes = AIVisibilityProbeSerializer(many=True, read_only=True)
    brand_visibility = BrandVisibilitySerializer(read_only=True)

    class Meta:
        model = AnalysisRun
        fields = [
            "id", "slug", "url", "brand_name", "country", "email", "run_type", "status", "progress",
            "composite_score", "error_message", "created_at", "updated_at",
            "page_scores", "competitors", "recommendations", "ai_probes",
            "brand_visibility", "llm_logs",
        ]


class StartAnalysisSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=2048)
    run_type = serializers.ChoiceField(
        choices=AnalysisRun.RunType.choices,
        default=AnalysisRun.RunType.SINGLE_PAGE,
    )
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    brand_name = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    country = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default=""
    )
    org_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_url(self, value):
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        return value

    def validate_email(self, value):
        return value.lower().strip() if value else ""

    def validate_country(self, value):
        return value.strip() if value else ""


# ============ Gamification Serializers ============

class AchievementSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    icon = serializers.CharField()
    points = serializers.IntegerField()


class UserGamificationSerializer(serializers.ModelSerializer):
    achievements_detail = serializers.SerializerMethodField()
    level_name = serializers.CharField(source="get_level_display")
    level_progress = serializers.FloatField()

    class Meta:
        model = UserGamification
        fields = [
            "user_email",
            "total_points",
            "points_this_week",
            "points_this_month",
            "level",
            "level_name",
            "current_level_points",
            "points_to_next_level",
            "level_progress",
            "current_streak",
            "longest_streak",
            "total_actions_completed",
            "total_actions_verified",
            "total_score_improvement",
            "achievements",
            "achievements_detail",
            "created_at",
            "updated_at",
        ]

    def get_achievements_detail(self, obj):
        return [
            {**ACHIEVEMENTS_INFO.get(code, {}), "code": code}
            for code in obj.achievements
            if code in ACHIEVEMENTS_INFO
        ]


class UserActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserAction
        fields = [
            "id",
            "action_type",
            "title",
            "description",
            "points_value",
            "status",
            "started_at",
            "completed_at",
            "verified_at",
            "score_before",
            "score_after",
            "score_improvement",
            "notes",
            "created_at",
            "analysis_run",
            "recommendation",
        ]
        read_only_fields = [
            "points_value", "started_at", "completed_at", "verified_at",
            "score_before", "score_after", "score_improvement", "created_at"
        ]


class CreateUserActionSerializer(serializers.Serializer):
    action_type = serializers.ChoiceField(choices=UserAction.ActionType.choices)
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    recommendation_id = serializers.IntegerField(required=False)
    analysis_run_id = serializers.IntegerField(required=False)
    score_before = serializers.FloatField(required=False)


class UpdateUserActionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=UserAction.ActionStatus.choices,
        required=False
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    score_after = serializers.FloatField(required=False)


class ActionTemplateSerializer(serializers.Serializer):
    action_type = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    points = serializers.IntegerField()
    category = serializers.CharField()


# ============ Helper Serializers ============

class ActionStatsSerializer(serializers.Serializer):
    total_actions = serializers.IntegerField()
    pending_actions = serializers.IntegerField()
    in_progress_actions = serializers.IntegerField()
    completed_actions = serializers.IntegerField()
    verified_actions = serializers.IntegerField()
    total_points = serializers.IntegerField()
    points_this_week = serializers.IntegerField()
    current_streak = serializers.IntegerField()
    level = serializers.IntegerField()
    level_name = serializers.CharField()
    level_progress = serializers.FloatField()
    recent_achievements = AchievementSerializer(many=True)


class PromptResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromptResult
        fields = [
            "id", "engine", "response_text", "brand_mentioned",
            "sentiment", "confidence", "rank_position", "checked_at",
        ]


class PromptTrackSerializer(serializers.ModelSerializer):
    results = PromptResultSerializer(many=True, read_only=True)
    visibility_pct = serializers.SerializerMethodField()
    avg_position = serializers.SerializerMethodField()
    sentiment_label = serializers.SerializerMethodField()
    ranking_label = serializers.SerializerMethodField()
    total_runs = serializers.SerializerMethodField()
    mentions = serializers.SerializerMethodField()

    class Meta:
        model = PromptTrack
        fields = [
            "id", "prompt_text", "is_custom", "score", "created_at", "results",
            "visibility_pct", "avg_position", "sentiment_label", "ranking_label",
            "total_runs", "mentions",
        ]

    def _score_data(self, obj):
        if not hasattr(obj, "_score_cache"):
            from .pipeline.prompt_tracker import compute_prompt_score
            results = list(obj.results.values("brand_mentioned", "sentiment", "rank_position", "confidence"))
            obj._score_cache = compute_prompt_score(results)
        return obj._score_cache

    def get_visibility_pct(self, obj):
        return self._score_data(obj)["visibility_pct"]

    def get_avg_position(self, obj):
        return self._score_data(obj)["avg_position"]

    def get_sentiment_label(self, obj):
        return self._score_data(obj)["sentiment"]

    def get_ranking_label(self, obj):
        return self._score_data(obj)["label"]

    def get_total_runs(self, obj):
        return self._score_data(obj)["total_runs"]

    def get_mentions(self, obj):
        return self._score_data(obj)["mentions"]


class AddPromptSerializer(serializers.Serializer):
    prompt_text = serializers.CharField(max_length=2000)


class ShareOfVoiceSerializer(serializers.Serializer):
    engine = serializers.CharField()
    total = serializers.IntegerField()
    mentioned = serializers.IntegerField()
    sov_pct = serializers.FloatField()


class CitationTrendPointSerializer(serializers.Serializer):
    week_start = serializers.DateField()
    engine = serializers.CharField()
    rate_pct = serializers.FloatField()


class BlogAutomationConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = BlogAutomationConfig
        fields = [
            "id",
            "user_email",
            "site_url",
            "topic",
            "keywords",
            "frequency_per_day",
            "publish_time",
            "mode",
            "publish_provider",
            "is_active",
            "last_queued_for",
            "created_at",
            "updated_at",
        ]


class BlogAutomationJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = BlogAutomationJob
        fields = [
            "id",
            "status",
            "scheduled_for",
            "provider",
            "mode",
            "topic",
            "keywords",
            "title",
            "slug",
            "meta_description",
            "excerpt",
            "content_markdown",
            "tags",
            "external_post_id",
            "external_post_url",
            "published_at",
            "error_message",
            "created_at",
            "updated_at",
        ]


class ScheduledAnalysisSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduledAnalysis
        fields = [
            "id", "email", "url", "brand_name", "frequency",
            "next_run_at", "last_run_at", "last_run_slug",
            "is_active", "created_at",
        ]
