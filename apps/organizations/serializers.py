from rest_framework import serializers

from .models import BrandProfile, Organization


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "url", "owner_email", "created_at"]
        # owner_email is returned but never writable through this serializer:
        # ownership is assigned at onboarding and must not be reassignable by
        # whoever can reach the edit endpoint (SEC-1a-0).
        read_only_fields = ["id", "slug", "owner_email", "created_at"]


class OrganizationUpdateSerializer(serializers.ModelSerializer):
    """Input for editing a brand. These two fields are the whole allowlist.

    Kept separate from ``OrganizationSerializer`` on purpose: a field added to
    the read shape later must not become editable by accident (SEC-1a).
    """

    class Meta:
        model = Organization
        fields = ["name", "url"]


class BrandProfileSerializer(serializers.ModelSerializer):
    """The 7 content sections are editable (a reviewer can correct copy or facts);
    workflow fields (status/confidence/last_verified_at/source_run/sources) are
    read-only over the API -- status changes only via the review endpoint."""

    class Meta:
        model = BrandProfile
        fields = [
            "status",
            "confidence",
            "last_verified_at",
            "source_run",
            "identity",
            "positioning",
            "audience",
            "voice",
            "canonical_facts",
            "competitors",
            "sources",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "status",
            "confidence",
            "last_verified_at",
            "source_run",
            "sources",
            "created_at",
            "updated_at",
        ]


class OnboardSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    url = serializers.URLField(required=False, allow_blank=True, default="")
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.lower().strip()

    def validate_name(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("Company name cannot be blank.")
        return cleaned

    def validate_url(self, value):
        return value.strip() if value else ""

    def create(self, validated_data):
        return Organization.objects.create(
            name=validated_data["name"],
            url=validated_data["url"],
            owner_email=validated_data["email"],
        )
