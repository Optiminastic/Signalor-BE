from rest_framework import serializers

from apps.organizations.models import Organization

from .models import (
    AgentActionLog,
    AgentMessage,
    GrowthCycle,
    GrowthProgram,
    GrowthTask,
    PositioningReport,
    WeeklySnapshot,
)
from .services.execution.publishers.resolver import connected_platform
from .services.outreach.channel import gmail_integration


class BrandSerializer(serializers.ModelSerializer):
    has_program = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = ["slug", "name", "url", "has_program"]

    def get_has_program(self, org: Organization) -> bool:
        return hasattr(org, "growth_program")


class GrowthProgramSerializer(serializers.ModelSerializer):
    brand = BrandSerializer(source="organization", read_only=True)
    publishing_platform = serializers.SerializerMethodField()
    gmail_sender = serializers.SerializerMethodField()

    def get_publishing_platform(self, program: GrowthProgram) -> str | None:
        return connected_platform(program.organization)

    def get_gmail_sender(self, program: GrowthProgram) -> str | None:
        integration = gmail_integration(program.organization)
        return integration.metadata.get("email") if integration else None

    class Meta:
        model = GrowthProgram
        fields = [
            "brand",
            "status",
            "publishing_platform",
            "gmail_sender",
            "channel_modes",
            "uplift_target_points",
            "kill_switch",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AnalysisRunStatusSerializer(serializers.Serializer):
    slug = serializers.CharField()
    status = serializers.CharField()
    progress = serializers.IntegerField()
    composite_score = serializers.FloatField(allow_null=True)
    error_message = serializers.CharField()
    created_at = serializers.DateTimeField()


class OnboardBrandSerializer(serializers.Serializer):
    url = serializers.CharField(max_length=2048)


class CreateProgramSerializer(serializers.Serializer):
    org_slug = serializers.CharField(max_length=32)


class PositioningReportSerializer(serializers.ModelSerializer):
    analysis_slug = serializers.CharField(source="analysis_run.slug", default=None, read_only=True)

    class Meta:
        model = PositioningReport
        fields = [
            "id",
            "status",
            "analysis_slug",
            "headline",
            "share_of_voice",
            "citation_gaps",
            "lost_prompts",
            "entity_gaps",
            "context_gaps",
            "error_message",
            "created_at",
        ]
        read_only_fields = fields


class GrowthTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = GrowthTask
        fields = [
            "id",
            "week",
            "scheduled_for",
            "channel",
            "tactic",
            "title",
            "rationale",
            "target",
            "target_url",
            "impact_score",
            "effort",
            "status",
            "evidence_url",
            "attempts",
            "result_payload",
        ]
        read_only_fields = fields


class GrowthCycleSerializer(serializers.ModelSerializer):
    tasks = GrowthTaskSerializer(many=True, read_only=True)

    class Meta:
        model = GrowthCycle
        fields = [
            "id",
            "status",
            "starts_on",
            "ends_on",
            "baseline_share_of_voice",
            "target_share_of_voice",
            "tasks",
        ]
        read_only_fields = fields


class UpdateTaskSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[GrowthTask.Status.PLANNED, GrowthTask.Status.SKIPPED])


class WeeklySnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = WeeklySnapshot
        fields = [
            "id",
            "week",
            "kind",
            "status",
            "share_of_voice",
            "engines",
            "rivals",
            "answers_checked",
            "delta_vs_baseline",
            "delta_vs_previous",
            "closed_gaps",
            "wins",
            "error_message",
            "started_at",
            "completed_at",
        ]
        read_only_fields = fields


class AgentActionLogSerializer(serializers.ModelSerializer):
    task_title = serializers.CharField(source="task.title", default="", read_only=True)

    class Meta:
        model = AgentActionLog
        fields = ["id", "channel", "action", "outcome", "task_title", "detail", "created_at"]
        read_only_fields = fields


class AgentMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentMessage
        fields = ["id", "role", "status", "content", "tool_trace", "created_at"]
        read_only_fields = fields


class PostMessageSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=4000, trim_whitespace=True)
