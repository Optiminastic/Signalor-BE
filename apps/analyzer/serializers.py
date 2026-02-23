from rest_framework import serializers

from .models import (
    AIVisibilityProbe,
    AnalysisRun,
    BrandVisibility,
    Competitor,
    PageScore,
    Recommendation,
)


class RecommendationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Recommendation
        fields = [
            "id", "pillar", "priority", "title", "description",
            "action", "impact_estimate", "category",
        ]


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
            "id", "name", "url", "industry", "composite_score", "scored", "page_score",
        ]


class AnalysisRunListSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnalysisRun
        fields = [
            "id", "url", "run_type", "status", "progress",
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
            "id", "url", "brand_name", "email", "run_type", "status", "progress",
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

    def validate_url(self, value):
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        return value

    def validate_email(self, value):
        return value.lower().strip() if value else ""
