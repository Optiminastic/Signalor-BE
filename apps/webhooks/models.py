"""Outbound webhooks: subscriptions and their delivery attempts.

Moved out of ``public_api`` so the API-key surface and the webhook surface are
separate apps (see docs/modularization-plan.md).

``db_table`` is pinned to the original ``public_api_*`` names on purpose. This is
a code move, not a schema change: renaming the tables would need a real migration
against production data for no functional gain. ``makemigrations --check`` must
stay clean and ``sqlmigrate`` must emit no DDL.
"""

from __future__ import annotations

import secrets

from django.db import models

# Fernet helpers. Top-level: integrations does not import webhooks, so there is
# no cycle - these were inline only because they came across with the model.
from apps.integrations.models import decrypt_token, encrypt_token


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
        # Was public_api.Webhook; keep the table so the move is code-only.
        db_table = "public_api_webhook"
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
        return decrypt_token(self.secret_encrypted)

    @classmethod
    def create_with_secret(
        cls,
        organization,
        url: str,
        events: list[str],
        created_by_email: str = "",
    ) -> tuple[Webhook, str]:
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
        # Was public_api.WebhookDelivery; keep the table.
        db_table = "public_api_webhookdelivery"
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


