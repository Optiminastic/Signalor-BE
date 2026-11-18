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
    # Articles on signalor.ai's own satellite sites that mention the client.
    "satellite_content",
    # Guest articles pitched to, written for and delivered to independent sites.
    "guest_outreach",
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
    # Who caused this entry, when a person did (an approval or a rejection).
    # Empty for everything the agent or the scheduler does on its own.
    actor_email = models.EmailField(blank=True, default="")
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


class KeywordSnapshot(models.Model):
    """One weekly read of keyword data for a brand, from DataForSEO (no customer connection).

    ``brand`` rows: keywords the brand ranks for. ``gaps`` rows: keywords the top
    competitors rank for in the top 10 where the brand doesn't. Kept per week so
    the Keywords tab never calls DataForSEO on page load and can mark new gaps.
    """

    class Kind(models.TextChoices):
        BRAND = "brand", "Brand keywords"
        GAPS = "gaps", "Keyword gaps"

    program = models.ForeignKey(GrowthProgram, on_delete=models.CASCADE, related_name="keyword_snapshots")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    rows = models.JSONField(default=list)
    meta = models.JSONField(default=dict)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fetched_at", "-id"]
        indexes = [models.Index(fields=["program", "kind", "-fetched_at"], name="growth_kw_program_kind_idx")]

    def __str__(self) -> str:
        return f"KeywordSnapshot<{self.program_id}:{self.kind}>"


class TrackedKeyword(models.Model):
    """A keyword whose Google position and AI Overview citations are checked weekly.

    The agent fills the list (``source=auto``); the owner can add (``owner``) or
    remove one. A removed keyword stays as ``active=False`` so the agent never
    adds it back.
    """

    class Source(models.TextChoices):
        AUTO = "auto", "Picked by the agent"
        OWNER = "owner", "Added by the owner"

    program = models.ForeignKey(GrowthProgram, on_delete=models.CASCADE, related_name="tracked_keywords")
    keyword = models.CharField(max_length=120)
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.AUTO)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["program", "keyword"], name="growth_tracked_keyword_unique")
        ]

    def __str__(self) -> str:
        return f"TrackedKeyword<{self.program_id}:{self.keyword}>"


class RankObservation(models.Model):
    """One week's Google position for one keyword and one domain (the brand or a competitor).

    ``position`` is 1-100, or null when not in the top 100.
    """

    class Kind(models.TextChoices):
        SEO = "seo", "Google position"

    program = models.ForeignKey(GrowthProgram, on_delete=models.CASCADE, related_name="rank_observations")
    week = models.DateField(help_text="Monday of the week observed.")
    kind = models.CharField(max_length=4, choices=Kind.choices)
    keyword = models.CharField(max_length=120)
    domain = models.CharField(max_length=255)
    position = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-week", "keyword"]
        constraints = [
            models.UniqueConstraint(
                fields=["program", "week", "kind", "keyword", "domain"], name="growth_rank_observation_unique"
            )
        ]
        indexes = [models.Index(fields=["program", "kind", "-week"], name="growth_rank_program_kind_idx")]

    def __str__(self) -> str:
        return f"RankObservation<{self.program_id}:{self.kind}:{self.keyword}:{self.domain}>"


class BrandMention(models.Model):
    """One page elsewhere on the internet that talks about the brand or a competitor.

    Found weekly by searching Google (web, news, Reddit) for the player's name, outside
    the player's own site; a model call drops pages not really about the player and marks
    the sentiment. ``domain`` is the player's own domain (who the mention is about).
    """

    class Channel(models.TextChoices):
        WEB = "web", "Web"
        NEWS = "news", "News"
        REDDIT = "reddit", "Reddit"

    class Sentiment(models.TextChoices):
        POSITIVE = "positive", "Positive"
        NEUTRAL = "neutral", "Neutral"
        NEGATIVE = "negative", "Negative"

    program = models.ForeignKey(GrowthProgram, on_delete=models.CASCADE, related_name="brand_mentions")
    week = models.DateField(help_text="Monday of the week it was found.")
    domain = models.CharField(max_length=255)
    url = models.URLField(max_length=1000)
    title = models.CharField(max_length=300)
    snippet = models.TextField(blank=True, default="")
    source = models.CharField(max_length=255, help_text="The site the mention is on.")
    channel = models.CharField(max_length=8, choices=Channel.choices)
    published = models.CharField(max_length=40, blank=True, default="", help_text="As Google shows it.")
    sentiment = models.CharField(max_length=8, choices=Sentiment.choices, default=Sentiment.NEUTRAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-week", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["program", "domain", "url"], name="growth_brand_mention_unique")
        ]
        indexes = [models.Index(fields=["program", "-week"], name="growth_mention_week_idx")]

    def __str__(self) -> str:
        return f"BrandMention<{self.program_id}:{self.domain}:{self.source}>"


# ── External actions and their approval (Phase 1 control foundation) ───────
# A task that needs something done outside Signalor (send an email, publish a
# page) first becomes a GrowthAction holding an exact, immutable draft. The
# brand owner approves or rejects that exact draft, and only an approved draft
# can be executed.

ACTION_ID_LENGTH = 12


def new_action_id() -> str:
    """Short, unguessable, URL-friendly id (same alphabet as chat ids)."""
    return "".join(secrets.choice(CONVERSATION_ID_ALPHABET) for _ in range(ACTION_ID_LENGTH))


class ImmutableRecordError(Exception):
    """An append-only record was about to be changed or deleted."""


class ImmutableQuerySet(models.QuerySet):
    """Blocks bulk changes. Rows of these tables are evidence: written once."""

    def update(self, **kwargs):
        raise ImmutableRecordError(f"{self.model.__name__} rows cannot be updated.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ImmutableRecordError(f"{self.model.__name__} rows cannot be updated.")

    def delete(self):
        raise ImmutableRecordError(f"{self.model.__name__} rows cannot be deleted.")


class ImmutableModel(models.Model):
    """Insert-only. A row leaves only when the brand it belongs to is deleted,
    which cascades at the database level and does not pass through here."""

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableRecordError(f"{type(self).__name__} rows cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError(f"{type(self).__name__} rows cannot be deleted.")


class GrowthAction(models.Model):
    """One external action a task wants to take, and where it stands."""

    class Kind(models.TextChoices):
        SEND_OUTREACH_EMAIL = "send_outreach_email", "Send outreach email"
        PUBLISH_ARTICLE = "publish_article", "Publish article"
        # A follow-up email in an existing outreach thread, approved on its own.
        SEND_FOLLOW_UP = "send_follow_up", "Send follow-up email"
        PUBLISH_SOCIAL_POST = "publish_social_post", "Publish social post"

    class State(models.TextChoices):
        AWAITING_APPROVAL = "awaiting_approval", "Awaiting approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        DISPATCHING = "dispatching", "Being sent"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        NEEDS_RECONCILIATION = "needs_reconciliation", "Outcome unknown"
        # Closed by the owner with the outcome still unknown. Never sent again.
        CANCELLED = "cancelled", "Closed, outcome unknown"
        # The approval ran out before sending. Never sent.
        EXPIRED = "expired", "Approval expired"
        # The owner withdrew the approval before sending. Never sent.
        REVOKED = "revoked", "Approval withdrawn"
        # A follow-up no longer needed because the contact replied. Never sent.
        STOPPED = "stopped", "Stopped, they replied"

    public_id = models.CharField(max_length=16, unique=True, default=new_action_id, editable=False)
    # Denormalized from task.cycle.program so every lookup can be scoped to the
    # brand in one filter. Set once, on creation, from the task itself.
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="growth_actions")
    task = models.ForeignKey(GrowthTask, on_delete=models.CASCADE, related_name="actions")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    connector = models.CharField(max_length=32)
    state = models.CharField(
        max_length=24, choices=State.choices, default=State.AWAITING_APPROVAL, db_index=True
    )
    current_version = models.ForeignKey(
        "ActionVersion", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # For a follow-up: the thread it continues and its step (1, 2, ...).
    thread = models.ForeignKey(
        OutreachThread, on_delete=models.CASCADE, null=True, blank=True, related_name="follow_up_actions"
    )
    sequence_no = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["organization", "state"], name="growth_action_org_state_idx")]
        constraints = [
            # One follow-up action per step of a thread.
            models.UniqueConstraint(
                fields=["thread", "sequence_no"],
                condition=models.Q(thread__isnull=False),
                name="growth_action_one_follow_up_per_step",
            )
        ]

    def __str__(self) -> str:
        return f"GrowthAction<{self.public_id}:{self.kind}:{self.state}>"


class ActionVersion(ImmutableModel):
    """The exact content and destination of an action. Never changes.

    An approval is bound to one version through its hash, so what was approved
    is provably what gets sent.
    """

    action = models.ForeignKey(GrowthAction, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    payload = models.JSONField()
    destination = models.JSONField()
    payload_hash = models.CharField(max_length=64)
    # Concerns shown to the owner with this version (e.g. fact-check doubts).
    # Not sent and not part of the hash: they describe the draft, they aren't it.
    review_notes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["action", "number"], name="growth_action_version_number_uniq")
        ]

    def __str__(self) -> str:
        return f"ActionVersion<{self.action_id}:v{self.number}>"


class ApprovalDecision(ImmutableModel):
    """A person's decision on one exact version of an action. Never changes."""

    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        # How the owner closed an action whose send outcome was unknown.
        RESOLVED_SENT = "resolved_sent", "Confirmed sent"
        RESOLVED_CANCELLED = "resolved_cancelled", "Closed, outcome unknown"
        REVOKED = "revoked", "Approval withdrawn"
        # Recorded by the system when an approval runs out.
        EXPIRED = "expired", "Approval expired"
        # Recorded by the system when a reply makes a follow-up unnecessary.
        STOPPED = "stopped", "Stopped, they replied"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="growth_decisions")
    action = models.ForeignKey(GrowthAction, on_delete=models.CASCADE, related_name="decisions")
    version = models.ForeignKey(ActionVersion, on_delete=models.CASCADE, related_name="decisions")
    # Copied from the version at decision time: the decision records what the
    # person saw, independently of the row it points at.
    payload_hash = models.CharField(max_length=64)
    decision = models.CharField(max_length=24, choices=Decision.choices)
    # Empty for a decision the system records (an expiry).
    actor_email = models.EmailField(blank=True)
    actor_role = models.CharField(max_length=16, default="owner")
    reason = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["action", "version"], name="growth_decision_action_ver_idx")]

    def __str__(self) -> str:
        return f"ApprovalDecision<{self.action_id}:{self.decision}>"


class ActionExecutionEvent(ImmutableModel):
    """One step of sending an action. Append-only; never changes.

    Each attempt writes ``claimed``, then ``provider_started`` immediately
    before the provider is called, then ``provider_result``. An attempt can
    instead end ``aborted`` (stopped before any outside call). The trail is
    what crash recovery reads: claimed but never started means nothing went
    out; started with no result means it may have.
    """

    class Event(models.TextChoices):
        CLAIMED = "claimed", "Claimed for sending"
        PROVIDER_STARTED = "provider_started", "Provider call started"
        PROVIDER_RESULT = "provider_result", "Provider call finished"
        ABORTED = "aborted", "Stopped before any outside call"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="growth_execution_events"
    )
    action = models.ForeignKey(GrowthAction, on_delete=models.CASCADE, related_name="execution_events")
    version = models.ForeignKey(ActionVersion, on_delete=models.CASCADE, related_name="execution_events")
    attempt = models.PositiveIntegerField()
    event = models.CharField(max_length=20, choices=Event.choices)
    # For provider_result: succeeded / failed / unknown. Empty otherwise.
    outcome = models.CharField(max_length=16, blank=True, default="")
    # Short reason or provider reference. Never a recipient address or token.
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            # One claim, one provider start and one result per attempt, at most.
            models.UniqueConstraint(
                fields=["action", "attempt", "event"],
                condition=models.Q(event__in=["claimed", "provider_started", "provider_result"]),
                name="growth_exec_event_once_per_attempt",
            )
        ]
        indexes = [models.Index(fields=["action", "attempt"], name="growth_exec_event_attempt_idx")]

    def __str__(self) -> str:
        return f"ActionExecutionEvent<{self.action_id}:#{self.attempt}:{self.event}>"


class DispatchAttemptLedger(ImmutableModel):
    """Append-only record of send attempts, paid for before they run.

    ``attempt`` is written before an attempt; if it can't be, the attempt
    doesn't happen. ``result`` records how it ended. ``unblock`` is an
    operator clearing a blocked action. ``seq`` numbers an action's attempts:
    it only goes up and is never reused. It refers to the action by public id
    with no foreign key, so writing a ticket never waits on the action's lock.
    """

    class Kind(models.TextChoices):
        ATTEMPT = "attempt", "Attempt reserved"
        RESULT = "result", "Attempt ended"
        UNBLOCK = "unblock", "Unblocked by an operator"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="growth_attempt_ledger"
    )
    action_public_id = models.CharField(max_length=16)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    seq = models.PositiveIntegerField()
    outcome = models.CharField(max_length=20, blank=True, default="")
    actor = models.CharField(max_length=64, blank=True, default="")
    reason = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["action_public_id", "kind", "seq"], name="growth_ledger_ticket_once"
            )
        ]
        indexes = [models.Index(fields=["action_public_id", "seq"], name="growth_ledger_action_seq_idx")]

    def __str__(self) -> str:
        return f"DispatchAttemptLedger<{self.action_public_id}:{self.kind}:{self.seq}>"


class ApprovalBatch(ImmutableModel):
    """A group of drafts for the owner to review together: one per brand per day.

    Grouping only. Approval stays one decision per action and exact version;
    a batch can reject several drafts at once, never approve them.
    """

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="growth_batches")
    day = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-day", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "day"], name="growth_batch_one_per_day"),
        ]

    def __str__(self) -> str:
        return f"ApprovalBatch<{self.organization_id}:{self.day}>"


class ApprovalBatchItem(ImmutableModel):
    """One exact version of an action in a batch. An edit adds a new item, never
    changes an old one; an item whose version is no longer current is superseded."""

    class Reason(models.TextChoices):
        PREPARED = "prepared", "Drafted"
        EDITED = "edited", "Edited"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="growth_batch_items"
    )
    batch = models.ForeignKey(ApprovalBatch, on_delete=models.CASCADE, related_name="items")
    action = models.ForeignKey(GrowthAction, on_delete=models.CASCADE, related_name="batch_items")
    version = models.ForeignKey(ActionVersion, on_delete=models.CASCADE, related_name="batch_items")
    added_reason = models.CharField(max_length=8, choices=Reason.choices, default=Reason.PREPARED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["batch", "action", "version"], name="growth_batch_item_once"),
        ]

    def __str__(self) -> str:
        return f"ApprovalBatchItem<{self.batch_id}:{self.action_id}:v{self.version_id}>"
