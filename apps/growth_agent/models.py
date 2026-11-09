"""
Growth Agent models.

Phase 0 only needs the program row: one autonomous off-page program per brand
(organization). Later phases hang positioning reports, monthly cycles, tasks
and weekly snapshots off this row. Like the GitHub agent, the database is the
agent's memory, so a worker restart never loses state.
"""

import secrets

from django.db import models

from apps.organizations.models import Organization

# Action channels the agent may run. This tuple is the code-level allowlist:
# anything not listed here can never be executed, whatever the LLM proposes.
CHANNELS = (
    "owned_content",
    "outreach_email",
    "entity",
    "directories",
    "journalist_requests",
    "owned_social",
    "review_requests",
    "unlinked_mentions",
    "community",
)


class ChannelMode(models.TextChoices):
    DRY_RUN = "dry_run", "Dry run (draft and log, never send)"
    APPROVAL = "approval", "Needs approval before sending"
    AUTONOMOUS = "autonomous", "Fully autonomous"


def default_channel_modes() -> dict[str, str]:
    """The monthly plan runs fully autonomously. Safety comes from automatic
    guardrails (brand-safety check, caps, budget, kill switch, audit log), not
    from human approval. DRY_RUN and APPROVAL remain available per channel."""
    return {channel: ChannelMode.AUTONOMOUS.value for channel in CHANNELS}


class GrowthProgram(models.Model):
    class Status(models.TextChoices):
        ONBOARDING = "onboarding", "Onboarding"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        STOPPED = "stopped", "Stopped"

    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="growth_program")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ONBOARDING, db_index=True)
    channel_modes = models.JSONField(default=default_channel_modes)
    # Monthly visibility uplift goal, in share-of-voice percentage points.
    uplift_target_points = models.PositiveSmallIntegerField(default=10)
    kill_switch = models.BooleanField(default=False)
    created_by_email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"GrowthProgram<{self.organization_id}:{self.status}>"


class PositioningReport(models.Model):
    """Where the brand stands in AI answers, built from one completed analysis.

    The first report of a program is the month's baseline that weekly tracking
    measures uplift against.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"

    program = models.ForeignKey(GrowthProgram, on_delete=models.CASCADE, related_name="positioning_reports")
    analysis_run = models.ForeignKey(
        "analyzer.AnalysisRun",
        on_delete=models.SET_NULL,
        null=True,
        related_name="growth_positioning_reports",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    headline = models.JSONField(default=dict)
    share_of_voice = models.JSONField(default=dict)
    citation_gaps = models.JSONField(default=list)
    lost_prompts = models.JSONField(default=list)
    entity_gaps = models.JSONField(default=list)
    context_gaps = models.JSONField(default=dict)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["program", "-created_at"])]

    def __str__(self) -> str:
        return f"PositioningReport<{self.program_id}:{self.status}>"


class GrowthCycle(models.Model):
    """One month of planned off-page work, measured against its positioning baseline."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    program = models.ForeignKey(GrowthProgram, on_delete=models.CASCADE, related_name="cycles")
    positioning_report = models.ForeignKey(
        PositioningReport, on_delete=models.SET_NULL, null=True, related_name="cycles"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    starts_on = models.DateField()
    ends_on = models.DateField()
    baseline_share_of_voice = models.FloatField(default=0.0)
    target_share_of_voice = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["program", "status"])]

    def __str__(self) -> str:
        return f"GrowthCycle<{self.program_id}:{self.starts_on}>"


class GrowthTask(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        READY = "ready", "Ready"
        AWAITING_APPROVAL = "awaiting_approval", "Awaiting approval"
        EXECUTING = "executing", "Executing"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    cycle = models.ForeignKey(GrowthCycle, on_delete=models.CASCADE, related_name="tasks")
    week = models.PositiveSmallIntegerField()
    scheduled_for = models.DateField()
    channel = models.CharField(max_length=32, choices=[(c, c) for c in CHANNELS])
    tactic = models.CharField(max_length=40)
    title = models.CharField(max_length=300)
    rationale = models.TextField(blank=True, default="")
    target = models.CharField(max_length=500, blank=True, default="")
    target_url = models.URLField(max_length=2048, blank=True, default="")
    prompts = models.JSONField(default=list, blank=True)
    impact_score = models.FloatField(default=0.0)
    effort = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED, db_index=True)
    draft_payload = models.JSONField(default=dict, blank=True)
    result_payload = models.JSONField(default=dict, blank=True)
    evidence_url = models.URLField(max_length=2048, blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["week", "scheduled_for", "-impact_score"]
        indexes = [
            models.Index(fields=["cycle", "week"]),
            models.Index(fields=["status", "scheduled_for"]),
        ]

    def __str__(self) -> str:
        return f"GrowthTask<{self.cycle_id}:w{self.week}:{self.tactic}>"


class WeeklySnapshot(models.Model):
    """One re-measurement of the brand's AI visibility during a cycle.

    ``week`` is the cycle week it measures (1..4). Deltas compare against the
    cycle's baseline and the previous completed snapshot.
    """

    class Kind(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        MANUAL = "manual", "Manual"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"

    cycle = models.ForeignKey(GrowthCycle, on_delete=models.CASCADE, related_name="snapshots")
    week = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.SCHEDULED)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    share_of_voice = models.FloatField(default=0.0)
    engines = models.JSONField(default=dict)
    rivals = models.JSONField(default=dict)
    answers_checked = models.PositiveIntegerField(default=0)
    delta_vs_baseline = models.FloatField(default=0.0)
    delta_vs_previous = models.FloatField(null=True, blank=True)
    closed_gaps = models.JSONField(default=list)
    wins = models.JSONField(default=list)
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["cycle", "-started_at"])]

    def __str__(self) -> str:
        return f"WeeklySnapshot<{self.cycle_id}:w{self.week}:{self.status}>"


class AgentActionLog(models.Model):
    """Append-only audit trail of everything the agent does or decides not to do.

    Never updated or deleted by the app; no endpoint mutates these rows.
    """

    class Outcome(models.TextChoices):
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        BLOCKED = "blocked", "Blocked by a guardrail"
        DRY_RUN = "dry_run", "Dry run (not sent)"

    program = models.ForeignKey(GrowthProgram, on_delete=models.CASCADE, related_name="action_logs")
    task = models.ForeignKey(GrowthTask, on_delete=models.SET_NULL, null=True, related_name="action_logs")
    channel = models.CharField(max_length=32)
    action = models.CharField(max_length=60)
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["program", "-created_at"]),
            models.Index(fields=["channel", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"AgentActionLog<{self.program_id}:{self.action}:{self.outcome}>"


CONVERSATION_ID_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
CONVERSATION_ID_LENGTH = 10
CONVERSATION_TITLE_CHARS = 80


def new_conversation_id() -> str:
    """Short, unguessable, URL-friendly id (no look-alike characters)."""
    return "".join(secrets.choice(CONVERSATION_ID_ALPHABET) for _ in range(CONVERSATION_ID_LENGTH))


class AgentConversation(models.Model):
    """One chat thread with the Growth Agent. A brand can have many; each has its own URL."""

    program = models.ForeignKey(GrowthProgram, on_delete=models.CASCADE, related_name="conversations")
    public_id = models.CharField(max_length=16, unique=True, default=new_conversation_id, editable=False)
    title = models.CharField(max_length=CONVERSATION_TITLE_CHARS, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [models.Index(fields=["program", "-updated_at"], name="growth_agen_program_conv_idx")]

    def __str__(self) -> str:
        return f"AgentConversation<{self.program_id}:{self.public_id}>"


class AgentMessage(models.Model):
    """One chat message between the brand owner and the Growth Agent.

    Assistant replies are created ``pending`` and filled by a background turn,
    so the chat never blocks on a multi-step tool loop.
    """

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"

    program = models.ForeignKey(GrowthProgram, on_delete=models.CASCADE, related_name="messages")
    conversation = models.ForeignKey(
        AgentConversation, on_delete=models.CASCADE, related_name="messages", null=True, blank=True
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.COMPLETE)
    content = models.TextField(blank=True, default="")
    tool_trace = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["program", "created_at"])]

    def __str__(self) -> str:
        return f"AgentMessage<{self.program_id}:{self.role}:{self.status}>"


class OutreachContact(models.Model):
    """Everyone the agent has emailed for a brand. Opt-outs are permanent."""

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="outreach_contacts")
    email = models.EmailField()
    domain = models.CharField(max_length=255, db_index=True)
    source_url = models.URLField(max_length=2048, blank=True, default="")
    opted_out = models.BooleanField(default=False)
    last_contacted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("organization", "email")]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"OutreachContact<{self.organization_id}:{self.domain}>"


class OutreachThread(models.Model):
    """One email conversation started by an outreach task."""

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FOLLOWED_UP = "followed_up", "Followed up"
        REPLIED = "replied", "Replied"
        OPTED_OUT = "opted_out", "Opted out"
        CLOSED = "closed", "Closed without reply"

    task = models.ForeignKey(GrowthTask, on_delete=models.CASCADE, related_name="outreach_threads")
    contact = models.ForeignKey(OutreachContact, on_delete=models.CASCADE, related_name="threads")
    gmail_thread_id = models.CharField(max_length=64)
    last_message_id = models.CharField(max_length=255, blank=True, default="")
    subject = models.CharField(max_length=300)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SENT, db_index=True)
    followups_sent = models.PositiveSmallIntegerField(default=0)
    next_follow_up_on = models.DateField(null=True, blank=True)
    replied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "next_follow_up_on"])]

    def __str__(self) -> str:
        return f"OutreachThread<{self.task_id}:{self.status}>"
