from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from rest_framework import serializers

from apps.analyzer.models import AnalysisRun, Recommendation

_url_validator = URLValidator(schemes=["http", "https"])

# Pillar fields on PageScore. Kept in sync with apps.analyzer.models.PageScore.
PILLARS = ("content", "schema", "eeat", "technical", "entity", "ai_visibility")


def _avg(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _aggregate_pillar_scores(run: AnalysisRun) -> dict[str, float | None]:
    """Average each pillar across the run's page scores."""
    pages = list(run.page_scores.all())
    out: dict[str, float | None] = {}
    for pillar in PILLARS:
        attr = f"{pillar}_score"
        out[pillar] = _avg([getattr(p, attr, None) for p in pages])
    return out


class CreateAnalysisSerializer(serializers.Serializer):
    url = serializers.CharField(max_length=2048)
    run_type = serializers.ChoiceField(
        choices=AnalysisRun.RunType.choices,
        default=AnalysisRun.RunType.SINGLE_PAGE,
    )
    brand_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    country = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")

    def validate_url(self, value):
        value = (value or "").strip()
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        try:
            _url_validator(value)
        except DjangoValidationError as err:
            raise serializers.ValidationError("Enter a valid URL.") from err
        return value


class AnalysisSummarySerializer(serializers.ModelSerializer):
    score = serializers.FloatField(source="composite_score", allow_null=True)
    pillar_scores = serializers.SerializerMethodField()
    recommendation_count = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisRun
        fields = [
            "slug",
            "url",
            "brand_name",
            "country",
            "run_type",
            "status",
            "progress",
            "score",
            "pillar_scores",
            "recommendation_count",
            "created_at",
            "updated_at",
        ]

    def get_pillar_scores(self, obj):
        return _aggregate_pillar_scores(obj)

    def get_recommendation_count(self, obj):
        return obj.recommendations.count()


class PublicRecommendationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Recommendation
        fields = [
            "id",
            "pillar",
            "priority",
            "title",
            "description",
            "action",
            "impact_estimate",
            "why",
            "category",
        ]
