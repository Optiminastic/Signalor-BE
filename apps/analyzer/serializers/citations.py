"""Share-of-voice and citation trend payloads."""

from rest_framework import serializers


class ShareOfVoiceSerializer(serializers.Serializer):
    engine = serializers.CharField()
    total = serializers.IntegerField()
    mentioned = serializers.IntegerField()
    sov_pct = serializers.FloatField()


class CitationTrendPointSerializer(serializers.Serializer):
    week_start = serializers.DateField()
    engine = serializers.CharField()
    rate_pct = serializers.FloatField()


class AiRecommendationEnginePointSerializer(serializers.Serializer):
    engine = serializers.CharField()
    total = serializers.IntegerField()
    mentioned = serializers.IntegerField()
    recommended = serializers.IntegerField()
    cited = serializers.IntegerField()
    recommendation_pct = serializers.FloatField()


class AiRecommendationSampleSerializer(serializers.Serializer):
    engine = serializers.CharField()
    prompt = serializers.CharField()
    quote = serializers.CharField()
    sentiment = serializers.CharField()


class AiRecommendationSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    mentioned = serializers.IntegerField()
    recommended = serializers.IntegerField()
    cited = serializers.IntegerField()
    mention_pct = serializers.FloatField()
    recommendation_pct = serializers.FloatField()
    citation_pct = serializers.FloatField()
    per_engine = AiRecommendationEnginePointSerializer(many=True)
    samples = AiRecommendationSampleSerializer(many=True)

