"""Leaf payloads nested by several others. No intra-package imports."""

from rest_framework import serializers

from ..models import (
    AIVisibilityProbe,
    BrandVisibility,
    PageScore,
)


class AIVisibilityProbeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIVisibilityProbe
        fields = [
            "id",
            "prompt_used",
            "llm_response",
            "brand_mentioned",
            "confidence",
        ]


class PageScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = PageScore
        fields = [
            "id",
            "url",
            "content_score",
            "content_details",
            "schema_score",
            "schema_details",
            "eeat_score",
            "eeat_details",
            "technical_score",
            "technical_details",
            "entity_score",
            "entity_details",
            "ai_visibility_score",
            "ai_visibility_details",
            "composite_score",
        ]


class BrandVisibilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = BrandVisibility
        fields = [
            "google_score",
            "google_details",
            "reddit_score",
            "reddit_details",
            "web_mentions_score",
            "web_mentions_details",
            "social_presence_details",
            "ai_brand_facts",
            "overall_score",
        ]

