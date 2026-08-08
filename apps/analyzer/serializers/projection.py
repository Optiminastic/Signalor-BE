"""Response schema for the 30-day visibility projection.

Output-only: the view hands it a plain dict built by
``services.projection.build_projection`` - never an ORM instance - so the
"no models in the response" rule holds by construction.
"""

from rest_framework import serializers


class ProjectionMetricSerializer(serializers.Serializer):
    """A current → target pair with its delta, in whole percentage points."""

    current = serializers.IntegerField()
    target = serializers.IntegerField()
    delta = serializers.IntegerField()


class ProjectionCompetitorsSerializer(serializers.Serializer):
    to_pass = serializers.IntegerField()
    names = serializers.ListField(child=serializers.CharField())
    total_ahead = serializers.IntegerField()


class ProjectionPromptsSerializer(serializers.Serializer):
    to_improve = serializers.IntegerField()
    total = serializers.IntegerField()


class ProjectionSerializer(serializers.Serializer):
    window_days = serializers.IntegerField()
    generated_at = serializers.CharField()
    visibility = ProjectionMetricSerializer()
    recommendation = ProjectionMetricSerializer()
    competitors = ProjectionCompetitorsSerializer()
    prompts = ProjectionPromptsSerializer()
