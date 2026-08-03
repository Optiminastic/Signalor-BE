"""Brand-level request payloads (entity resolution, IndexNow)."""

from django.core.validators import URLValidator
from rest_framework import serializers

_url_validator = URLValidator(schemes=["http", "https"])


class EntityResolutionRequestSerializer(serializers.Serializer):
    """Body for POST runs/s/<slug>/entity-resolution/.

    ``engines`` is optional; omitted means "every engine". It is bounded to the
    known set because each name costs one billable call, and validated for shape
    because a scalar (``{"engines": 5}``) reached a ``for`` loop downstream and
    raised TypeError - a 500 for what is plainly a bad request.
    """

    engines = serializers.ListField(
        child=serializers.CharField(max_length=32),
        required=False,
        allow_empty=False,
        max_length=16,
    )

    def validate_engines(self, value):
        from core.llm.client import ANSWER_ENGINES

        unknown = [e for e in value if e not in ANSWER_ENGINES]
        if unknown:
            raise serializers.ValidationError(
                f"Unknown engines: {sorted(unknown)}. Choose from {sorted(ANSWER_ENGINES)}."
            )
        return value


class IndexNowSubmitSerializer(serializers.Serializer):
    """Body for POST runs/s/<slug>/indexnow/.

    ``urls`` is optional; omitted means "every page this run knows about". The
    host is re-checked server-side in ``indexnow.submit`` - this only rejects
    malformed shapes so they cannot reach it as a 500.
    """

    urls = serializers.ListField(
        child=serializers.URLField(max_length=2000),
        required=False,
        allow_empty=False,
        max_length=500,
    )

