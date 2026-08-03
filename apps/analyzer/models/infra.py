"""Infrastructure-backing tables with no domain of their own."""

from django.db import models
from pgvector.django import VectorField

from core.llm.config import EMBEDDING_DIMENSIONS

from .run import AnalysisRun


class ChatMessage(models.Model):
    """Persistent AI assistant chat history per analysis run."""

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="chat_messages")
    role = models.CharField(max_length=12, choices=Role.choices)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["analysis_run", "created_at"])]

    def __str__(self):
        return f"ChatMessage<run={self.analysis_run_id} {self.role} {self.created_at:%Y-%m-%d %H:%M}>"


class LLMResponseCache(models.Model):
    """Cached LLM responses for repeat prompts (Epic 7).

    Two-tier lookup: an exact ``prompt_hash`` match (free, zero-risk) and, failing that,
    a pgvector cosine search over ``prompt_embedding`` with a conservative similarity
    floor. Both are constrained to the same SCOPE -- (purpose, model_key, organization) --
    which is the safety property: two brands can never share a cache entry.

    Opt-in per call site (``ask_llm(cache=True)``); entries expire via ``expires_at``.
    """

    # Scope. A lookup must match all three, so a hit can never cross brands/purposes.
    purpose = models.CharField(max_length=128, db_index=True)
    model_key = models.CharField(max_length=64)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="llm_response_cache",
    )

    # sha256 of the normalized prompt; the exact-match fast path.
    prompt_hash = models.CharField(max_length=64, db_index=True)
    prompt_text = models.TextField(blank=True, default="")  # truncated, for debugging
    prompt_embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS, null=True, blank=True)
    response_text = models.TextField()

    hit_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["purpose", "model_key", "organization", "prompt_hash"],
                name="uniq_llm_cache_scope_hash",
            )
        ]
        indexes = [
            models.Index(fields=["purpose", "model_key", "organization"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"LLMResponseCache<{self.purpose}:{self.prompt_hash[:8]}>"

