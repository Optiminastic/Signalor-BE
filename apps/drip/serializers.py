from rest_framework import serializers


class PricingViewedSerializer(serializers.Serializer):
    email = serializers.EmailField()
    amplitude_user_id = serializers.CharField(required=False, allow_blank=True, max_length=64)
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=120)
    domain = serializers.CharField(required=False, allow_blank=True, max_length=255)
    geo_score = serializers.FloatField(required=False, allow_null=True)
    fix_count = serializers.IntegerField(required=False, allow_null=True)
    top_competitor = serializers.CharField(required=False, allow_blank=True, max_length=255)
    competitor_list = serializers.CharField(required=False, allow_blank=True)
    cms_platform = serializers.CharField(required=False, allow_blank=True, max_length=32)
    top_recommendation_title = serializers.CharField(required=False, allow_blank=True, max_length=512)
    issue_count = serializers.IntegerField(required=False, allow_null=True)
    competitor_count = serializers.IntegerField(required=False, allow_null=True)


class CheckoutStartedSerializer(serializers.Serializer):
    email = serializers.EmailField()
