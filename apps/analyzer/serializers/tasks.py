"""Recommendations, user actions and gamification payloads."""

from rest_framework import serializers

from ..models import (
    ACHIEVEMENTS_INFO,
    Recommendation,
    UserAction,
    UserGamification,
)
from ..services.attribution import attribution_for


class RecommendationSerializer(serializers.ModelSerializer):
    can_auto_fix = serializers.SerializerMethodField()
    code_fixable = serializers.SerializerMethodField()
    steps = serializers.SerializerMethodField()

    class Meta:
        model = Recommendation
        fields = [
            "id",
            "pillar",
            "priority",
            "title",
            "description",
            "action",
            "category",
            "can_auto_fix",
            "code_fixable",
            "why",
            "steps",
            "xp_reward",
            "difficulty",
            "estimated_minutes",
            "finding_code",
            "finding_key",
            "source",
            "evidence",
            "affected_pages",
            "generated_content",
            "last_checked_at",
            "daily_priority_rank",
            "is_top_fix",
        ]

    # Title keywords that indicate manual-only recommendations
    MANUAL_TITLE_KEYWORDS = [
        "sitemap",
        "enable https",
        "page load speed",
        "improve page load",
        "crawler blocked",
        "blocks automated",
        "too slow to crawl",
        "wikipedia",
        "reddit",
        "google ai overview",
        "brand into ai",
        "social profile",
        "brand website signal",
    ]

    # Fix types that can actually be auto-applied on any URL
    AUTO_FIX_TITLE_KEYWORDS = [
        "llms.txt",
        "robots.txt",
        "ai meta",
        "ai-meta",
        "ai crawler",
        "ai bot",
        "gptbot",
        "claudebot",
        "noindex",
    ]

    # Fix types that need a product/page URL — cannot auto-fix on homepage
    HOMEPAGE_MANUAL_TITLE_KEYWORDS = [
        "meta description",
        "seo title",
        "title tag",
        "meta title",
        "json-ld",
        "structured data",
        "schema",
        "faq",
        "expert quote",
        "author attribution",
        "first-hand",
        "about page",
        "contact page",
        "content",
        "keyword stuff",
        "review",
        "comparison",
        "shipping",
        "product description",
    ]

    def get_steps(self, obj):
        """Coerce steps into the {n, title, detail, xp} shape the frontend expects.

        AI-insight recommendations (source="ai_insight", see
        apps/analyzer/services/overview_insights.py) store `steps` as a plain
        list of strings, but the frontend schema requires objects — normalize
        here rather than letting validation fail the whole run.
        """
        steps = obj.steps or []
        normalized = []
        for i, step in enumerate(steps):
            if isinstance(step, dict):
                normalized.append(step)
            else:
                normalized.append({"n": i + 1, "title": str(step), "detail": "", "xp": 0})
        return normalized

    def get_code_fixable(self, obj):
        """True when the GitHub code agent can fix this finding (in-repo edit)."""
        # Asked through core.ports.code_fix: whether a finding is code-fixable is
        # the agent's knowledge, and github_agent sits above analyzer.
        from core.ports import code_fix

        return code_fix.is_agent_fixable(obj.finding_code or "")

    def get_can_auto_fix(self, obj):
        title_lower = (obj.title or "").lower()

        # Always manual
        for kw in self.MANUAL_TITLE_KEYWORDS:
            if kw in title_lower:
                return False

        # Check if this is a homepage analysis
        run = obj.analysis_run
        run_url = (run.url or "") if run else ""
        is_homepage = False
        if run_url:
            try:
                from urllib.parse import urlparse

                path = urlparse(run_url).path.rstrip("/")
                is_homepage = not path or path == ""
            except Exception:
                pass

        # On homepage: only specific fix types can auto-apply
        if is_homepage:
            for kw in self.AUTO_FIX_TITLE_KEYWORDS:
                if kw in title_lower:
                    return True
            # Schema category on homepage = theme extension (auto)
            # but schema issues like "missing schema" on homepage = manual
            return False

        # On product/page URLs: most things can be auto-fixed
        return True


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
    # What completing this task improves. Read off the linked Recommendation so
    # the list can answer "why is this worth doing?" without a second request —
    # previously only the recommendation's id crossed the wire.
    pillar = serializers.CharField(source="recommendation.pillar", read_only=True, default="")
    finding_code = serializers.CharField(
        source="recommendation.finding_code", read_only=True, default=""
    )
    priority = serializers.CharField(source="recommendation.priority", read_only=True, default="")
    attribution = serializers.SerializerMethodField()

    def get_attribution(self, obj) -> dict:
        rec = obj.recommendation
        return attribution_for(
            getattr(rec, "pillar", "") or "",
            getattr(rec, "finding_code", "") or "",
            # Carries the tracked prompt for GEO findings, so the row can name
            # the prompt it targets instead of a generic pillar label.
            getattr(rec, "evidence", None),
        )

    class Meta:
        model = UserAction
        fields = [
            "id",
            "action_type",
            "title",
            "description",
            "points_value",
            "status",
            "assignee_email",
            "started_at",
            "completed_at",
            "verified_at",
            "score_before",
            "score_after",
            "score_improvement",
            "notes",
            "verification_message",
            "created_at",
            "analysis_run",
            "recommendation",
            "pillar",
            "finding_code",
            "priority",
            "attribution",
        ]
        read_only_fields = [
            "points_value",
            "started_at",
            "completed_at",
            "verified_at",
            "score_before",
            "score_after",
            "score_improvement",
            "verification_message",
            "created_at",
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
    status = serializers.ChoiceField(choices=UserAction.ActionStatus.choices, required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    score_after = serializers.FloatField(required=False)


class ActionTemplateSerializer(serializers.Serializer):
    action_type = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    points = serializers.IntegerField()
    category = serializers.CharField()


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

