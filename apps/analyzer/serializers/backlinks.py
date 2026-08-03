"""Blog automation config and job payloads."""

from rest_framework import serializers

from ..models import (
    BlogAutomationConfig,
    BlogAutomationJob,
)


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

