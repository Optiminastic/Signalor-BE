"""Response and request shapes for draft actions and the owner's decisions.

Read shapes are built from the stored version only, so what the owner reviews
is exactly what would be sent. A recipient is never returned as an address.
"""

from rest_framework import serializers

from .models import ApprovalDecision, GrowthAction
from .services.control import actions

HASH_LENGTH = 64


class DecisionRequestSerializer(serializers.Serializer):
    """The owner states which exact draft they reviewed."""

    version = serializers.IntegerField(min_value=1)
    payload_hash = serializers.CharField(min_length=HASH_LENGTH, max_length=HASH_LENGTH)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class ApprovalDecisionSerializer(serializers.ModelSerializer):
    version = serializers.IntegerField(source="version.number", read_only=True)

    class Meta:
        model = ApprovalDecision
        fields = ["decision", "version", "payload_hash", "actor_role", "reason", "created_at"]
        read_only_fields = fields


class ActionTaskSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    channel = serializers.CharField()
    tactic = serializers.CharField()
    target = serializers.CharField()
    scheduled_for = serializers.DateField()
    status = serializers.CharField()


class ActionSerializer(serializers.ModelSerializer):
    """One draft action with the exact content under review."""

    id = serializers.CharField(source="public_id", read_only=True)
    task = ActionTaskSerializer(read_only=True)
    version = serializers.SerializerMethodField()
    payload_hash = serializers.SerializerMethodField()
    content = serializers.SerializerMethodField()

    class Meta:
        model = GrowthAction
        fields = [
            "id",
            "kind",
            "connector",
            "state",
            "task",
            "version",
            "payload_hash",
            "content",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_version(self, action: GrowthAction) -> int | None:
        return action.current_version.number if action.current_version else None

    def get_payload_hash(self, action: GrowthAction) -> str:
        return action.current_version.payload_hash if action.current_version else ""

    def get_content(self, action: GrowthAction) -> dict:
        return actions.present(action, action.current_version) if action.current_version else {}


class ActionDetailSerializer(ActionSerializer):
    """An action with its decision history."""

    decisions = ApprovalDecisionSerializer(many=True, read_only=True)

    class Meta(ActionSerializer.Meta):
        fields = [*ActionSerializer.Meta.fields, "decisions"]
        read_only_fields = fields


class TaskActionSummarySerializer(serializers.ModelSerializer):
    """The small summary attached to a task in the plan."""

    id = serializers.CharField(source="public_id", read_only=True)
    version = serializers.SerializerMethodField()
    payload_hash = serializers.SerializerMethodField()

    class Meta:
        model = GrowthAction
        fields = ["id", "kind", "state", "version", "payload_hash"]
        read_only_fields = fields

    def get_version(self, action: GrowthAction) -> int | None:
        return action.current_version.number if action.current_version else None

    def get_payload_hash(self, action: GrowthAction) -> str:
        return action.current_version.payload_hash if action.current_version else ""
