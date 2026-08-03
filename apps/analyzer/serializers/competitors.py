"""Competitor payloads."""

from rest_framework import serializers

from ..models import (
    Competitor,
)
from .base import PageScoreSerializer


class CompetitorSerializer(serializers.ModelSerializer):
    page_score = PageScoreSerializer(read_only=True)

    class Meta:
        model = Competitor
        fields = [
            "id",
            "name",
            "url",
            "industry",
            "tier",
            "target_market",
            "geography",
            "pricing_model",
            "estimated_revenue_band",
            "positioning",
            "relevance_score",
            "composite_score",
            "scored",
            "page_score",
        ]

