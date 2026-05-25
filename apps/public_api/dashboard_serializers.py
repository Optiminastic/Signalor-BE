from rest_framework import serializers

from .models import ApiKey, NextJsDeployment, Webhook


class ApiKeyListSerializer(serializers.ModelSerializer):
    """Safe to expose — never includes the plaintext or full hash."""

    class Meta:
        model = ApiKey
        fields = [
            "id",
            "name",
            "environment",
            "key_prefix",
            "key_last4",
            "created_by_email",
            "created_at",
            "last_used_at",
            "revoked_at",
        ]


class CreateApiKeySerializer(serializers.Serializer):
    email = serializers.EmailField()
    org_id = serializers.IntegerField(required=False, allow_null=True)
    name = serializers.CharField(max_length=120)
    environment = serializers.ChoiceField(
        choices=ApiKey.Environment.choices,
        default=ApiKey.Environment.LIVE,
    )

    def validate_email(self, value):
        return (value or "").lower().strip()

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Name is required.")
        return value


class WebhookListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Webhook
        fields = [
            "id",
            "url",
            "events",
            "secret_last4",
            "is_active",
            "created_by_email",
            "created_at",
            "last_delivered_at",
        ]


class CreateWebhookSerializer(serializers.Serializer):
    email = serializers.EmailField()
    org_id = serializers.IntegerField(required=False, allow_null=True)
    url = serializers.URLField(max_length=2048)
    events = serializers.ListField(
        child=serializers.ChoiceField(choices=Webhook.Event.choices),
        min_length=1,
    )

    def validate_email(self, value):
        return (value or "").lower().strip()


class NextJsDeploymentListSerializer(serializers.ModelSerializer):
    """Dashboard view of recent deploys. Includes the linked analysis slug
    so the UI can deep-link to the run page."""

    analysis_slug = serializers.SerializerMethodField()
    analysis_status = serializers.SerializerMethodField()
    analysis_score = serializers.SerializerMethodField()

    class Meta:
        model = NextJsDeployment
        fields = [
            "id",
            "commit_sha",
            "environment",
            "url",
            "host",
            "status",
            "error_message",
            "build_metadata",
            "analysis_slug",
            "analysis_status",
            "analysis_score",
            "created_at",
            "deployed_at",
        ]

    def get_analysis_slug(self, obj):
        return obj.analysis_run.slug if obj.analysis_run else None

    def get_analysis_status(self, obj):
        return obj.analysis_run.status if obj.analysis_run else None

    def get_analysis_score(self, obj):
        return obj.analysis_run.composite_score if obj.analysis_run else None
