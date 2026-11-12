"""Response and request shapes for draft actions and the owner's decisions.

Read shapes are built from the stored version only, so what the owner reviews
is exactly what would be sent. A recipient is never returned as an address.
"""

from rest_framework import serializers

from .models import ApprovalDecision, GrowthAction
from .services.control import actions, approvals, resolution, timeline

HASH_LENGTH = 64


class DecisionRequestSerializer(serializers.Serializer):
    """The owner states which exact draft they reviewed."""

    version = serializers.IntegerField(min_value=1)
    payload_hash = serializers.CharField(min_length=HASH_LENGTH, max_length=HASH_LENGTH)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class ResolutionRequestSerializer(DecisionRequestSerializer):
    """How the owner closes an action whose send outcome is unknown.

    There is deliberately no "send it again" choice.
    """

    resolution = serializers.ChoiceField(choices=resolution.RESOLUTIONS)


class EditRequestSerializer(serializers.Serializer):
    """The owner's edit, naming the exact version it was made on.

    Which fields are allowed, and their limits, depend on the kind of action
    and are checked in ``services.control.editing``.
    """

    version = serializers.IntegerField(min_value=1)
    payload_hash = serializers.CharField(min_length=HASH_LENGTH, max_length=HASH_LENGTH)
    fields = serializers.DictField(
        child=serializers.CharField(allow_blank=True, trim_whitespace=False, max_length=10_000),
        allow_empty=False,
    )


class OwnerContactSerializer(serializers.Serializer):
    """The owner's answer when no contact email was found: an email to use, or skip."""

    email = serializers.EmailField(required=False)
    skip = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if bool(attrs.get("email")) == bool(attrs.get("skip")):
            raise serializers.ValidationError("Give an email, or skip, but not both.")
        return attrs


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
    # When the approval in force runs out; null unless the action is approved.
    approval_expires_at = serializers.SerializerMethodField()

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
            "approval_expires_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_version(self, action: GrowthAction) -> int | None:
        return action.current_version.number if action.current_version else None

    def get_payload_hash(self, action: GrowthAction) -> str:
        return action.current_version.payload_hash if action.current_version else ""

    def get_approval_expires_at(self, action: GrowthAction) -> str | None:
        if action.state != GrowthAction.State.APPROVED:
            return None
        expires_at = approvals.approval_expires_at(action)
        return serializers.DateTimeField().to_representation(expires_at) if expires_at else None

    def get_content(self, action: GrowthAction) -> dict:
        return actions.present(action, action.current_version) if action.current_version else {}


class ActionDetailSerializer(ActionSerializer):
    """An action with its decision history and everything that happened to it."""

    decisions = ApprovalDecisionSerializer(many=True, read_only=True)
    timeline = serializers.SerializerMethodField()
    send_attempts = serializers.SerializerMethodField()

    class Meta(ActionSerializer.Meta):
        fields = [*ActionSerializer.Meta.fields, "decisions", "timeline", "send_attempts"]
        read_only_fields = fields

    def get_timeline(self, action: GrowthAction) -> list[dict]:
        return timeline.timeline(action)

    def get_send_attempts(self, action: GrowthAction) -> dict:
        return timeline.send_attempts(action)


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
