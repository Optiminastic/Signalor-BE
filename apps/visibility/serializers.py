from rest_framework import serializers

from .models import VisibilityCheck


class VisibilityCheckListSerializer(serializers.ModelSerializer):
    class Meta:
        model = VisibilityCheck
        fields = [
            "id", "brand_name", "brand_url", "status", "progress",
            "overall_score", "created_at",
        ]


class VisibilityCheckDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = VisibilityCheck
        fields = [
            "id", "brand_name", "brand_url", "email", "status", "progress",
            "google_score", "google_details",
            "reddit_score", "reddit_details",
            "medium_score", "medium_details",
            "overall_score", "error_message",
            "created_at", "updated_at",
        ]


class StartVisibilityCheckSerializer(serializers.Serializer):
    brand_name = serializers.CharField(max_length=255)
    brand_url = serializers.URLField(max_length=2048)
    email = serializers.EmailField(required=False, allow_blank=True, default="")

    def validate_brand_url(self, value):
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        return value

    def validate_brand_name(self, value):
        return value.strip()

    def validate_email(self, value):
        return value.lower().strip() if value else ""
