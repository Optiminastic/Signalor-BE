"""Tracked prompts, their results and citations."""

from rest_framework import serializers

from ..models import (
    PromptCitation,
    PromptResult,
    PromptTrack,
)


class PromptCitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromptCitation
        fields = [
            "id",
            "url",
            "domain",
            "title",
            "snippet",
            "position",
            "is_brand",
            "is_competitor",
        ]


class PromptResultSerializer(serializers.ModelSerializer):
    response_text = serializers.SerializerMethodField()
    citations = PromptCitationSerializer(many=True, read_only=True)

    def get_response_text(self, obj):
        text = obj.response_text or ""
        return text[:500] if len(text) > 500 else text

    class Meta:
        model = PromptResult
        fields = [
            "id",
            "engine",
            "response_text",
            "brand_mentioned",
            "sentiment",
            "confidence",
            "rank_position",
            "checked_at",
            "citations",
        ]


class PromptResultFullSerializer(serializers.ModelSerializer):
    citations = PromptCitationSerializer(many=True, read_only=True)

    class Meta:
        model = PromptResult
        fields = [
            "id",
            "engine",
            "response_text",
            "brand_mentioned",
            "sentiment",
            "confidence",
            "rank_position",
            "checked_at",
            "citations",
        ]


class PromptTrackSerializer(serializers.ModelSerializer):
    results = PromptResultSerializer(many=True, read_only=True)
    intent = serializers.SerializerMethodField()
    prompt_type = serializers.SerializerMethodField()
    visibility_pct = serializers.SerializerMethodField()
    avg_position = serializers.SerializerMethodField()
    sentiment_label = serializers.SerializerMethodField()
    ranking_label = serializers.SerializerMethodField()
    total_runs = serializers.SerializerMethodField()
    mentions = serializers.SerializerMethodField()
    # 5-factor breakdown (computed live so they reflect the latest results)
    factor_authority = serializers.SerializerMethodField()
    factor_content_quality = serializers.SerializerMethodField()
    factor_structural = serializers.SerializerMethodField()
    factor_semantic = serializers.SerializerMethodField()
    factor_third_party = serializers.SerializerMethodField()

    class Meta:
        model = PromptTrack
        fields = [
            "id",
            "prompt_text",
            "is_custom",
            "intent",
            "prompt_type",
            "score",
            "created_at",
            "results",
            "visibility_pct",
            "avg_position",
            "sentiment_label",
            "ranking_label",
            "total_runs",
            "mentions",
            # 5-factor scores
            "factor_authority",
            "factor_content_quality",
            "factor_structural",
            "factor_semantic",
            "factor_third_party",
        ]

    def _taxonomy(self, obj):
        """Recompute from prompt + run so labels stay accurate when rules improve."""
        if not hasattr(obj, "_taxonomy_cache"):
            from ..pipeline.prompt_tracker import classify_prompt_intent_and_type

            run = getattr(obj, "analysis_run", None)
            if run is None:
                obj._taxonomy_cache = ("informational", "organic")
            else:
                brand = (getattr(run, "brand_name", None) or "").strip()
                url = (getattr(run, "url", None) or "").strip()
                obj._taxonomy_cache = classify_prompt_intent_and_type(
                    obj.prompt_text,
                    brand,
                    url,
                )
        return obj._taxonomy_cache

    def get_intent(self, obj):
        return self._taxonomy(obj)[0]

    def get_prompt_type(self, obj):
        return self._taxonomy(obj)[1]

    def _score_data(self, obj):
        if not hasattr(obj, "_score_cache"):
            from ..pipeline.prompt_tracker import compute_prompt_score

            # Read from the prefetched .results manager rather than .values()
            # — .values() re-queries the DB even when results are prefetched.
            results = [
                {
                    "brand_mentioned": r.brand_mentioned,
                    "sentiment": r.sentiment,
                    "rank_position": r.rank_position,
                    "confidence": r.confidence,
                    "engine": r.engine,
                }
                for r in obj.results.all()
            ]
            obj._score_cache = compute_prompt_score(results)
        return obj._score_cache

    def get_visibility_pct(self, obj):
        return self._score_data(obj)["visibility_pct"]

    def get_avg_position(self, obj):
        return self._score_data(obj)["avg_position"]

    def get_sentiment_label(self, obj):
        return self._score_data(obj)["sentiment"]

    def get_factor_authority(self, obj):
        return self._score_data(obj)["authority_score"]

    def get_factor_content_quality(self, obj):
        return self._score_data(obj)["content_quality_score"]

    def get_factor_structural(self, obj):
        return self._score_data(obj)["structural_score"]

    def get_factor_semantic(self, obj):
        return self._score_data(obj)["semantic_score"]

    def get_factor_third_party(self, obj):
        return self._score_data(obj)["third_party_score"]

    def get_ranking_label(self, obj):
        return self._score_data(obj)["label"]

    def get_total_runs(self, obj):
        return self._score_data(obj)["total_runs"]

    def get_mentions(self, obj):
        return self._score_data(obj)["mentions"]


class AddPromptSerializer(serializers.Serializer):
    prompt_text = serializers.CharField(max_length=2000)

