from rest_framework import serializers

from ..models import NextJsDeployment


class RegisterResponseSerializer(serializers.Serializer):
    """What the SDK gets on its first call. Determines what to inject in HTML
    and what to publish at /llms.txt without the dev configuring anything."""

    organization = serializers.DictField(child=serializers.JSONField())
    schema_defaults = serializers.DictField(child=serializers.JSONField())
    llms_txt_template = serializers.CharField()
    sitemap_overrides = serializers.DictField(child=serializers.JSONField())


class DeployRequestSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=2048, required=False, allow_blank=True, default="")
    commit_sha = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    environment = serializers.ChoiceField(
        choices=NextJsDeployment.Environment.choices,
        required=False,
        default=NextJsDeployment.Environment.PRODUCTION,
    )
    host = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    build_metadata = serializers.DictField(required=False, default=dict)


class DeploymentResponseSerializer(serializers.ModelSerializer):
    analysis_slug = serializers.SerializerMethodField()
    status_url = serializers.SerializerMethodField()

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
            "analysis_slug",
            "status_url",
            "created_at",
            "deployed_at",
        ]

    def get_analysis_slug(self, obj):
        return obj.analysis_run.slug if obj.analysis_run else None

    def get_status_url(self, obj):
        if not obj.analysis_run:
            return None
        # Public read endpoint built in Phase 1 — same Bearer key the SDK
        # already holds works here, no extra auth.
        return f"/api/v1/public/analyses/{obj.analysis_run.slug}/"


class _PageMetadataSerializer(serializers.Serializer):
    path = serializers.CharField(max_length=2048)
    title = serializers.CharField(max_length=512, required=False, allow_blank=True, default="")
    h1 = serializers.CharField(max_length=512, required=False, allow_blank=True, default="")
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")
    # Free-form hints the SDK can include (e.g. ["Product", "FAQPage"]).
    # The analyzer treats these as suggestions, not authority.
    schema_hints = serializers.ListField(
        child=serializers.CharField(max_length=80),
        required=False,
        default=list,
    )


class BulkMetadataRequestSerializer(serializers.Serializer):
    deployment_id = serializers.IntegerField()
    pages = serializers.ListField(
        child=_PageMetadataSerializer(),
        min_length=1,
        # Big bulk uploads waste DB rows for paths that won't matter; cap
        # at 500 per call. Clients chunk if they have more.
        max_length=500,
    )
