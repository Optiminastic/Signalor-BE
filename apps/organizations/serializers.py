from rest_framework import serializers

from .models import Organization


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "url", "owner_email", "created_at"]
        read_only_fields = ["id", "slug", "created_at"]


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
