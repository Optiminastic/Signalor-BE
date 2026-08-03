"""Run lifecycle payloads: list, detail, start."""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from ..models import (
    AgentLogEntry,
    AnalysisRun,
    ScheduledAnalysis,
)
from .base import AIVisibilityProbeSerializer, BrandVisibilitySerializer, PageScoreSerializer
from .brand import _url_validator
from .competitors import CompetitorSerializer
from .tasks import RecommendationSerializer


class AnalysisRunListSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnalysisRun
        fields = [
            "id",
            "slug",
            "url",
            "country",
            "run_type",
            "status",
            "progress",
            "composite_score",
            "created_at",
        ]


class AnalysisRunDetailSerializer(serializers.ModelSerializer):
    page_scores = PageScoreSerializer(many=True, read_only=True)
    competitors = CompetitorSerializer(many=True, read_only=True)
    recommendations = RecommendationSerializer(many=True, read_only=True)
    ai_probes = AIVisibilityProbeSerializer(many=True, read_only=True)
    brand_visibility = BrandVisibilitySerializer(read_only=True)
    display_brand_name = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisRun
        fields = [
            "id",
            "slug",
            "url",
            "brand_name",
            "display_brand_name",
            "country",
            "email",
            "run_type",
            "status",
            "progress",
            "composite_score",
            "error_message",
            "created_at",
            "updated_at",
            "page_scores",
            "competitors",
            "recommendations",
            "ai_probes",
            "brand_visibility",
        ]

    def get_display_brand_name(self, obj):
        from apps.analyzer.pipeline.brand_naming import visibility_brand_label

        return visibility_brand_label(getattr(obj, "url", "") or "", getattr(obj, "brand_name", "") or "")


class StartAnalysisSerializer(serializers.Serializer):
    url = serializers.CharField(max_length=2048)
    run_type = serializers.ChoiceField(
        choices=AnalysisRun.RunType.choices,
        default=AnalysisRun.RunType.SINGLE_PAGE,
    )
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    brand_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    country = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    org_id = serializers.IntegerField(required=False, allow_null=True)
    # When true (onboarding / post-checkout launch), require org ownership, URL match, brand, and prompts.
    verify_org_workspace = serializers.BooleanField(required=False, default=False)
    prompts = serializers.ListField(
        child=serializers.CharField(max_length=500),
        required=False,
        allow_empty=True,
    )
    storefront_password = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_url(self, value):
        from apps.analyzer.url_guard import SSRFValidationError, validate_public_url

        value = value.strip()
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        try:
            _url_validator(value)
        except DjangoValidationError as err:
            raise serializers.ValidationError("Enter a valid URL.") from err
        # SSRF guard: reject private/loopback/link-local/metadata targets before
        # this URL is ever stored on the run and fetched server-side.
        try:
            validate_public_url(value)
        except SSRFValidationError as err:
            raise serializers.ValidationError(
                "That URL can't be analyzed — enter a public website address."
            ) from err
        return value

    def validate_email(self, value):
        return value.lower().strip() if value else ""

    def validate_country(self, value):
        return value.strip() if value else ""

    def validate(self, attrs):
        from apps.organizations.models import Organization

        from ..workspace_urls import normalize_workspace_url

        verify = attrs.get("verify_org_workspace") is True
        raw_prompts = attrs.get("prompts")
        if not raw_prompts:
            raw_prompts = []
        cleaned = [p.strip() for p in raw_prompts if isinstance(p, str) and p.strip()]
        if len(cleaned) > 15:
            raise serializers.ValidationError({"prompts": "You can track at most 15 prompts."})

        if verify:
            org_id = attrs.get("org_id")
            if not org_id:
                raise serializers.ValidationError(
                    {"org_id": "Create your workspace first, then launch analysis from onboarding."}
                )
            email = (attrs.get("email") or "").strip().lower()
            if not email:
                raise serializers.ValidationError(
                    {"email": "Sign in to continue — we need your account email to verify your workspace."}
                )
            brand = (attrs.get("brand_name") or "").strip()
            if not brand:
                raise serializers.ValidationError(
                    {
                        "brand_name": "Brand name is required. Go back to the first step and enter your company name."
                    }
                )
            if len(cleaned) < 1:
                raise serializers.ValidationError(
                    {"prompts": "Add at least one tracking prompt before launching."}
                )

            org = Organization.objects.filter(pk=org_id).first()
            if not org:
                raise serializers.ValidationError(
                    {"org_id": "Workspace not found. Complete company setup, then try again."}
                )
            if org.owner_email.strip().lower() != email:
                raise serializers.ValidationError(
                    {"org_id": "This workspace does not belong to your account."}
                )

            org_url = (org.url or "").strip()
            if org_url:
                req_norm = normalize_workspace_url(attrs["url"])
                org_norm = normalize_workspace_url(org_url)
                if req_norm != org_norm:
                    raise serializers.ValidationError(
                        {
                            "url": "Website URL must match your workspace URL. Go back and correct it, or update your workspace."
                        }
                    )

        attrs["_cleaned_prompts"] = cleaned
        return attrs


class ScheduledAnalysisSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduledAnalysis
        fields = [
            "id",
            "email",
            "url",
            "brand_name",
            "frequency",
            "next_run_at",
            "last_run_at",
            "last_run_slug",
            "is_active",
            "created_at",
        ]


class AgentLogEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentLogEntry
        fields = ["id", "bot_name", "path", "status_code", "ts", "source"]

