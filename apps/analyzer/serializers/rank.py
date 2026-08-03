"""Rank audit payloads."""

from rest_framework import serializers

from ..models import (
    RankAudit,
    RankQuery,
    RankResult,
)


class RankResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = RankResult
        fields = [
            "id",
            "surface",
            "position",
            "url",
            "domain",
            "title",
            "snippet",
            "engine",
            "response_text",
            "sentiment",
            "is_brand_mentioned",
            "competitors_mentioned",
            "upvotes",
            "subreddit",
            "checked_at",
        ]


class RankQuerySerializer(serializers.ModelSerializer):
    results = RankResultSerializer(many=True, read_only=True)

    class Meta:
        model = RankQuery
        fields = [
            "id",
            "prompt_text",
            "rank",
            "brand_mention_count",
            "status",
            "error_message",
            "results",
        ]


class RankAuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = RankAudit
        fields = [
            "id",
            "status",
            "progress",
            "total_queries",
            "queries_done",
            "avg_brand_mentions",
            "avg_top3_brand_rate",
            "started_at",
            "finished_at",
            "created_at",
            "error_message",
        ]

