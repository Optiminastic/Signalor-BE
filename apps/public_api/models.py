import hashlib
import secrets

from django.db import models
from django.utils import timezone


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class ApiKey(models.Model):
    class Environment(models.TextChoices):
        LIVE = "live", "Live"
        TEST = "test", "Test"

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField(max_length=120)
    environment = models.CharField(
        max_length=10,
        choices=Environment.choices,
        default=Environment.LIVE,
    )

    # Stored at issue time so the dashboard can display "sk_live_abcd…xyz9"
    # without ever holding the plaintext token after creation.
    key_prefix = models.CharField(max_length=20)
    key_last4 = models.CharField(max_length=4)
    # SHA-256 of the plaintext token. Token entropy is 32 random bytes
    # (b64-urlsafe), so a single hash is sufficient — bcrypt would only add
    # latency without raising the floor.
    key_hash = models.CharField(max_length=64, unique=True, db_index=True)

    created_by_email = models.EmailField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.key_prefix}…{self.key_last4})"

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def revoke(self) -> None:
        self.revoked_at = timezone.now()
        self.save(update_fields=["revoked_at"])

    def touch(self) -> None:
        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at"])

    @classmethod
    def generate(
        cls,
        organization,
        name: str,
        environment: str = Environment.LIVE,
        created_by_email: str = "",
    ) -> tuple["ApiKey", str]:
        """
        Mint a new key. Returns (instance, plaintext_token).

        The plaintext is returned ONLY here — the dashboard must show it once
        and never store it server-side again.
        """
        raw = secrets.token_urlsafe(32)
        plaintext = f"sk_{environment}_{raw}"
        prefix = plaintext[:12]
        last4 = plaintext[-4:]
        instance = cls.objects.create(
            organization=organization,
            name=name,
            environment=environment,
            key_prefix=prefix,
            key_last4=last4,
            key_hash=_hash_token(plaintext),
            created_by_email=(created_by_email or "").lower().strip(),
        )
        return instance, plaintext

    @classmethod
    def authenticate(cls, plaintext: str) -> "ApiKey | None":
        if not plaintext or not plaintext.startswith("sk_"):
            return None
        try:
            key = cls.objects.select_related("organization").get(
                key_hash=_hash_token(plaintext),
            )
        except cls.DoesNotExist:
            return None
        if key.is_revoked:
            return None
        return key


class PublicApiUsage(models.Model):
    api_key = models.ForeignKey(
        ApiKey,
        on_delete=models.CASCADE,
        related_name="usage",
    )
    # Denormalized so usage queries don't need a join when an API key has
    # been revoked and we still want billing/audit history per org.
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="public_api_usage",
    )
    route = models.CharField(max_length=80)
    method = models.CharField(max_length=10)
    status_code = models.IntegerField()
    duration_ms = models.IntegerField(default=0)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["organization", "-timestamp"]),
            models.Index(fields=["api_key", "-timestamp"]),
        ]

    def __str__(self):
        return f"{self.route} [{self.status_code}] @ {self.timestamp:%Y-%m-%d %H:%M}"


class Webhook(models.Model):
    class Event(models.TextChoices):
        ANALYSIS_COMPLETED = "analysis.completed", "Analysis completed"

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="webhooks",
    )
    url = models.URLField(max_length=2048)
    # Subscribed event names. Stored as a JSON list rather than M2M so adding
    # new event types is a code-only change with no extra migration.
    events = models.JSONField(default=list)
    # Encrypted signing secret. Plaintext returned exactly once at creation;
    # uses the same Fernet key already wired for the integrations app.
    secret_encrypted = models.TextField()
    secret_last4 = models.CharField(max_length=4)

    created_by_email = models.EmailField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "-created_at"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.url} ({self.organization_id})"

    def subscribes_to(self, event: str) -> bool:
        return self.is_active and event in (self.events or [])

    def get_secret(self) -> str:
        from apps.integrations.models import decrypt_token

        return decrypt_token(self.secret_encrypted)

    @classmethod
    def create_with_secret(
        cls,
        organization,
        url: str,
        events: list[str],
        created_by_email: str = "",
    ) -> tuple["Webhook", str]:
        from apps.integrations.models import encrypt_token

        plaintext = f"whsec_{secrets.token_urlsafe(32)}"
        instance = cls.objects.create(
            organization=organization,
            url=url,
            events=events,
            secret_encrypted=encrypt_token(plaintext),
            secret_last4=plaintext[-4:],
            created_by_email=(created_by_email or "").lower().strip(),
        )
        return instance, plaintext


class WebhookDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        SUCCESS = "success"
        FAILED = "failed"

    webhook = models.ForeignKey(
        Webhook,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    event = models.CharField(max_length=80)
    # Resource being delivered. For analysis.completed this is the AnalysisRun slug.
    # Free-form so future events (e.g. recommendation.created) can reuse the row.
    resource_id = models.CharField(max_length=80)

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    attempts = models.IntegerField(default=0)
    response_status = models.IntegerField(null=True, blank=True)
    response_body_preview = models.CharField(max_length=500, blank=True, default="")
    error_message = models.CharField(max_length=500, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        # Idempotency: a given (webhook, event, resource) is delivered exactly once,
        # so the signal can fire freely without producing duplicates.
        unique_together = [("webhook", "event", "resource_id")]
        indexes = [
            models.Index(fields=["webhook", "-created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.event} → {self.webhook_id} [{self.status}]"


class NextJsDeployment(models.Model):
    """A deploy reported by the @signalor/nextjs SDK or CLI.

    Captures what was deployed (commit, env, URL) and links to the
    AnalysisRun we kick off in response. Page metadata pushed by the SDK
    via /metadata/bulk lives in ``pages_metadata`` so the analyzer can
    use it instead of crawling.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ANALYZING = "analyzing", "Analyzing"
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"

    class Environment(models.TextChoices):
        PRODUCTION = "production", "Production"
        PREVIEW = "preview", "Preview"
        DEVELOPMENT = "development", "Development"

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="nextjs_deployments",
    )
    # Nullable: the deploy row is created first; the analyzer task fills
    # this in once it kicks off the run.
    analysis_run = models.ForeignKey(
        "analyzer.AnalysisRun",
        on_delete=models.SET_NULL,
        related_name="nextjs_deployments",
        null=True,
        blank=True,
    )

    # All optional — local dev has no commit, some hosts don't expose a
    # deploy URL until the build finishes.
    commit_sha = models.CharField(max_length=40, blank=True, default="")
    environment = models.CharField(
        max_length=20,
        choices=Environment.choices,
        default=Environment.PRODUCTION,
    )
    url = models.URLField(max_length=2048, blank=True, default="")
    # "vercel", "netlify", "self-hosted", etc. — reported by the SDK from
    # platform env vars (VERCEL_ENV, NETLIFY, etc.).
    host = models.CharField(max_length=40, blank=True, default="")
    # Anything the SDK wants to attach (build duration, node version, etc.).
    # Surfaced in the dashboard deployments timeline.
    build_metadata = models.JSONField(default=dict, blank=True)

    # Page-level metadata pushed via /metadata/bulk:
    # [{"path": "/", "title": "...", "h1": "...", "description": "...",
    #   "schema_hints": ["Organization", "WebSite"]}, ...]
    # Lets the analyzer skip crawling pages we already have data for.
    pages_metadata = models.JSONField(default=list, blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    deployed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "-created_at"]),
            models.Index(fields=["commit_sha"]),
        ]

    def __str__(self):
        commit = self.commit_sha[:8] if self.commit_sha else "no-commit"
        return f"NextJsDeployment {self.organization_id} {commit} [{self.environment}]"
